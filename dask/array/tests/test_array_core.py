from __future__ import absolute_import, division, print_function

import pytest
pytest.importorskip('numpy')

from operator import add, sub
from tempfile import mkdtemp
import shutil
import os

from toolz import merge
from toolz.curried import identity

import dask
import dask.array as da
from dask.async import get_sync
from dask.array.core import *
from dask.utils import raises, ignoring, tmpfile


inc = lambda x: x + 1


def same_keys(a, b):
    def key(k):
        if isinstance(k, str):
            return (k, -1, -1, -1)
        else:
            return k
    return sorted(a.dask, key=key) == sorted(b.dask, key=key)


def test_getem():
    assert getem('X', (2, 3), shape=(4, 6)) == \
    {('X', 0, 0): (getarray, 'X', (slice(0, 2), slice(0, 3))),
     ('X', 1, 0): (getarray, 'X', (slice(2, 4), slice(0, 3))),
     ('X', 1, 1): (getarray, 'X', (slice(2, 4), slice(3, 6))),
     ('X', 0, 1): (getarray, 'X', (slice(0, 2), slice(3, 6)))}


def test_top():
    assert top(inc, 'z', 'ij', 'x', 'ij', numblocks={'x': (2, 2)}) == \
        {('z', 0, 0): (inc, ('x', 0, 0)),
         ('z', 0, 1): (inc, ('x', 0, 1)),
         ('z', 1, 0): (inc, ('x', 1, 0)),
         ('z', 1, 1): (inc, ('x', 1, 1))}

    assert top(add, 'z', 'ij', 'x', 'ij', 'y', 'ij',
                numblocks={'x': (2, 2), 'y': (2, 2)}) == \
        {('z', 0, 0): (add, ('x', 0, 0), ('y', 0, 0)),
         ('z', 0, 1): (add, ('x', 0, 1), ('y', 0, 1)),
         ('z', 1, 0): (add, ('x', 1, 0), ('y', 1, 0)),
         ('z', 1, 1): (add, ('x', 1, 1), ('y', 1, 1))}

    assert top(dotmany, 'z', 'ik', 'x', 'ij', 'y', 'jk',
                    numblocks={'x': (2, 2), 'y': (2, 2)}) == \
        {('z', 0, 0): (dotmany, [('x', 0, 0), ('x', 0, 1)],
                                [('y', 0, 0), ('y', 1, 0)]),
         ('z', 0, 1): (dotmany, [('x', 0, 0), ('x', 0, 1)],
                                [('y', 0, 1), ('y', 1, 1)]),
         ('z', 1, 0): (dotmany, [('x', 1, 0), ('x', 1, 1)],
                                [('y', 0, 0), ('y', 1, 0)]),
         ('z', 1, 1): (dotmany, [('x', 1, 0), ('x', 1, 1)],
                                [('y', 0, 1), ('y', 1, 1)])}

    assert top(identity, 'z', '', 'x', 'ij', numblocks={'x': (2, 2)}) ==\
        {('z',): (identity, [[('x', 0, 0), ('x', 0, 1)],
                             [('x', 1, 0), ('x', 1, 1)]])}


def test_top_supports_broadcasting_rules():
    assert top(add, 'z', 'ij', 'x', 'ij', 'y', 'ij',
                numblocks={'x': (1, 2), 'y': (2, 1)}) == \
        {('z', 0, 0): (add, ('x', 0, 0), ('y', 0, 0)),
         ('z', 0, 1): (add, ('x', 0, 1), ('y', 0, 0)),
         ('z', 1, 0): (add, ('x', 0, 0), ('y', 1, 0)),
         ('z', 1, 1): (add, ('x', 0, 1), ('y', 1, 0))}


def test_concatenate3():
    x = np.array([1, 2])
    assert concatenate3([[x, x, x],
                            [x, x, x]]).shape == (2, 6)

    x = np.array([[1, 2]])
    assert concatenate3([[x, x, x],
                            [x, x, x]]).shape == (2, 6)


def test_concatenate3_on_scalars():
    assert eq(concatenate3([1, 2]), np.array([1, 2]))


def eq(a, b):
    if isinstance(a, Array):
        adt = a._dtype
        a = a.compute(get=dask.get)
    else:
        adt = getattr(a, 'dtype', None)
    if isinstance(b, Array):
        bdt = b._dtype
        b = b.compute(get=dask.get)
    else:
        bdt = getattr(b, 'dtype', None)

    if not str(adt) == str(bdt):
        return False

    try:
        return np.allclose(a, b)
    except TypeError:
        pass

    c = a == b

    if isinstance(c, np.ndarray):
        return c.all()
    else:
        return c


def test_chunked_dot_product():
    x = np.arange(400).reshape((20, 20))
    o = np.ones((20, 20))

    d = {'x': x, 'o': o}

    getx = getem('x', (5, 5), shape=(20, 20))
    geto = getem('o', (5, 5), shape=(20, 20))

    result = top(dotmany, 'out', 'ik', 'x', 'ij', 'o', 'jk',
                 numblocks={'x': (4, 4), 'o': (4, 4)})

    dsk = merge(d, getx, geto, result)
    out = dask.get(dsk, [[('out', i, j) for j in range(4)] for i in range(4)])

    assert eq(np.dot(x, o), concatenate3(out))


def test_chunked_transpose_plus_one():
    x = np.arange(400).reshape((20, 20))

    d = {'x': x}

    getx = getem('x', (5, 5), shape=(20, 20))

    f = lambda x: x.T + 1
    comp = top(f, 'out', 'ij', 'x', 'ji', numblocks={'x': (4, 4)})

    dsk = merge(d, getx, comp)
    out = dask.get(dsk, [[('out', i, j) for j in range(4)] for i in range(4)])

    assert eq(concatenate3(out), x.T + 1)


def test_transpose():
    x = np.arange(240).reshape((4, 6, 10))
    d = da.from_array(x, (2, 3, 4))

    assert eq(d.transpose((2, 0, 1)),
              x.transpose((2, 0, 1)))
    assert same_keys(d.transpose((2, 0, 1)), d.transpose((2, 0, 1)))


def test_broadcast_dimensions_works_with_singleton_dimensions():
    argpairs = [('x', 'i')]
    numblocks = {'x': ((1,),)}
    assert broadcast_dimensions(argpairs, numblocks) == {'i': (1,)}


def test_broadcast_dimensions():
    argpairs = [('x', 'ij'), ('y', 'ij')]
    d = {'x': ('Hello', 1), 'y': (1, (2, 3))}
    assert broadcast_dimensions(argpairs, d) == {'i': 'Hello', 'j': (2, 3)}


def test_Array():
    shape = (1000, 1000)
    chunks = (100, 100)
    name = 'x'
    dsk = merge({name: 'some-array'}, getem(name, chunks, shape=shape))
    a = Array(dsk, name, chunks, shape=shape)

    assert a.numblocks == (10, 10)

    assert a._keys() == [[('x', i, j) for j in range(10)]
                                     for i in range(10)]

    assert a.chunks == ((100,) * 10, (100,) * 10)

    assert a.shape == shape

    assert len(a) == shape[0]


def test_uneven_chunks():
    a = Array({}, 'x', chunks=(3, 3), shape=(10, 10))
    assert a.chunks == ((3, 3, 3, 1), (3, 3, 3, 1))


def test_numblocks_suppoorts_singleton_block_dims():
    shape = (100, 10)
    chunks = (10, 10)
    name = 'x'
    dsk = merge({name: 'some-array'}, getem(name, shape=shape, chunks=chunks))
    a = Array(dsk, name, chunks, shape=shape)

    assert set(concat(a._keys())) == set([('x', i, 0) for i in range(100//10)])


def test_keys():
    dsk = dict((('x', i, j), ()) for i in range(5) for j in range(6))
    dx = Array(dsk, 'x', chunks=(10, 10), shape=(50, 60))
    assert dx._keys() == [[(dx.name, i, j) for j in range(6)]
                                          for i in range(5)]
    d = Array({}, 'x', (), shape=())
    assert d._keys() == [('x',)]


def test_Array_computation():
    a = Array({('x', 0, 0): np.eye(3)}, 'x', shape=(3, 3), chunks=(3, 3))
    assert eq(np.array(a), np.eye(3))
    assert isinstance(a.compute(), np.ndarray)
    assert float(a[0, 0]) == 1


def test_stack():
    a, b, c = [Array(getem(name, chunks=(2, 3), shape=(4, 6)),
                     name, shape=(4, 6), chunks=(2, 3))
                for name in 'ABC']

    s = stack([a, b, c], axis=0)

    colon = slice(None, None, None)

    assert s.shape == (3, 4, 6)
    assert s.chunks == ((1, 1, 1), (2, 2), (3, 3))
    assert s.dask[(s.name, 0, 1, 0)] == (getarray, ('A', 1, 0),
                                          (None, colon, colon))
    assert s.dask[(s.name, 2, 1, 0)] == (getarray, ('C', 1, 0),
                                          (None, colon, colon))
    assert same_keys(s, stack([a, b, c], axis=0))

    s2 = stack([a, b, c], axis=1)
    assert s2.shape == (4, 3, 6)
    assert s2.chunks == ((2, 2), (1, 1, 1), (3, 3))
    assert s2.dask[(s2.name, 0, 1, 0)] == (getarray, ('B', 0, 0),
                                            (colon, None, colon))
    assert s2.dask[(s2.name, 1, 1, 0)] == (getarray, ('B', 1, 0),
                                            (colon, None, colon))
    assert same_keys(s2, stack([a, b, c], axis=1))

    s2 = stack([a, b, c], axis=2)
    assert s2.shape == (4, 6, 3)
    assert s2.chunks == ((2, 2), (3, 3), (1, 1, 1))
    assert s2.dask[(s2.name, 0, 1, 0)] == (getarray, ('A', 0, 1),
                                            (colon, colon, None))
    assert s2.dask[(s2.name, 1, 1, 2)] == (getarray, ('C', 1, 1),
                                            (colon, colon, None))
    assert same_keys(s2, stack([a, b, c], axis=2))

    assert raises(ValueError, lambda: stack([a, b, c], axis=3))

    assert set(b.dask.keys()).issubset(s2.dask.keys())

    assert stack([a, b, c], axis=-1).chunks == \
            stack([a, b, c], axis=2).chunks


def test_short_stack():
    x = np.array([1])
    d = da.from_array(x, chunks=(1,))
    s = da.stack([d])
    assert s.shape == (1, 1)
    assert Array._get(s.dask, s._keys())[0][0].shape == (1, 1)


def test_stack_scalars():
    d = da.arange(4, chunks=2)

    s = da.stack([d.mean(), d.sum()])

    assert s.compute().tolist() == [np.arange(4).mean(), np.arange(4).sum()]


def test_concatenate():
    a, b, c = [Array(getem(name, chunks=(2, 3), shape=(4, 6)),
                     name, shape=(4, 6), chunks=(2, 3))
                for name in 'ABC']

    x = concatenate([a, b, c], axis=0)

    assert x.shape == (12, 6)
    assert x.chunks == ((2, 2, 2, 2, 2, 2), (3, 3))
    assert x.dask[(x.name, 0, 1)] == ('A', 0, 1)
    assert x.dask[(x.name, 5, 0)] == ('C', 1, 0)
    assert same_keys(x, concatenate([a, b, c], axis=0))

    y = concatenate([a, b, c], axis=1)

    assert y.shape == (4, 18)
    assert y.chunks == ((2, 2), (3, 3, 3, 3, 3, 3))
    assert y.dask[(y.name, 1, 0)] == ('A', 1, 0)
    assert y.dask[(y.name, 1, 5)] == ('C', 1, 1)
    assert same_keys(y, concatenate([a, b, c], axis=1))

    assert set(b.dask.keys()).issubset(y.dask.keys())

    assert concatenate([a, b, c], axis=-1).chunks == \
            concatenate([a, b, c], axis=1).chunks

    assert raises(ValueError, lambda: concatenate([a, b, c], axis=2))


def test_vstack():
    x = np.arange(5)
    y = np.ones(5)
    a = da.arange(5, chunks=2)
    b = da.ones(5, chunks=2)

    assert eq(np.vstack((x, y)), da.vstack((a, b)))
    assert eq(np.vstack((x, y[None, :])), da.vstack((a, b[None, :])))


def test_hstack():
    x = np.arange(5)
    y = np.ones(5)
    a = da.arange(5, chunks=2)
    b = da.ones(5, chunks=2)

    assert eq(np.hstack((x[None, :], y[None, :])),
              da.hstack((a[None, :], b[None, :])))
    assert eq(np.hstack((x, y)), da.hstack((a, b)))


def test_dstack():
    x = np.arange(5)
    y = np.ones(5)
    a = da.arange(5, chunks=2)
    b = da.ones(5, chunks=2)

    assert eq(np.dstack((x[None, None, :], y[None, None, :])),
              da.dstack((a[None, None, :], b[None, None, :])))
    assert eq(np.dstack((x[None, :], y[None, :])),
              da.dstack((a[None, :], b[None, :])))
    assert eq(np.dstack((x, y)), da.dstack((a, b)))


def test_take():
    x = np.arange(400).reshape((20, 20))
    a = from_array(x, chunks=(5, 5))

    assert eq(np.take(x, 3, axis=0), take(a, 3, axis=0))
    assert eq(np.take(x, [3, 4, 5], axis=-1), take(a, [3, 4, 5], axis=-1))
    assert raises(ValueError, lambda: take(a, 3, axis=2))
    assert same_keys(take(a, [3, 4, 5], axis=-1), take(a, [3, 4, 5], axis=-1))


def test_compress():
    x = np.arange(25).reshape((5, 5))
    a = from_array(x, chunks=(2, 2))

    assert eq(np.compress([True, False, True, False, True], x, axis=0),
              da.compress([True, False, True, False, True], a, axis=0))
    assert eq(np.compress([True, False, True, False, True], x, axis=1),
              da.compress([True, False, True, False, True], a, axis=1))
    assert eq(np.compress([True, False], x, axis=1),
              da.compress([True, False], a, axis=1))

    with pytest.raises(NotImplementedError):
        da.compress([True, False], a)
    with pytest.raises(ValueError):
        da.compress([True, False], a, axis=100)
    with pytest.raises(ValueError):
        da.compress([[True], [False]], a, axis=100)


def test_binops():
    a = Array(dict((('a', i), np.array([''])) for i in range(3)),
              'a', chunks=((1, 1, 1),))
    b = Array(dict((('b', i), np.array([''])) for i in range(3)),
              'b', chunks=((1, 1, 1),))

    result = elemwise(add, a, b, name='c')
    assert result.dask == merge(a.dask, b.dask,
                                dict((('c', i), (add, ('a', i), ('b', i)))
                                     for i in range(3)))

    result = elemwise(pow, a, 2, name='c')
    assert result.dask[('c', 0)][1] == ('a', 0)
    f = result.dask[('c', 0)][0]
    assert f(10) == 100


def test_isnull():
    x = np.array([1, np.nan])
    a = from_array(x, chunks=(2,))
    with ignoring(ImportError):
        assert eq(isnull(a), np.isnan(x))
        assert eq(notnull(a), ~np.isnan(x))


def test_isclose():
    x = np.array([0, np.nan, 1, 1.5])
    y = np.array([1e-9, np.nan, 1, 2])
    a = from_array(x, chunks=(2,))
    b = from_array(y, chunks=(2,))
    assert eq(da.isclose(a, b, equal_nan=True),
              np.isclose(x, y, equal_nan=True))


def test_broadcast_shapes():
    assert (3, 4, 5) == broadcast_shapes((3, 4, 5), (4, 1), ())
    assert (3, 4) == broadcast_shapes((3, 1), (1, 4), (4,))
    assert (5, 6, 7, 3, 4) == broadcast_shapes((3, 1), (), (5, 6, 7, 1, 4))
    assert raises(ValueError, lambda: broadcast_shapes((3,), (3, 4)))
    assert raises(ValueError, lambda: broadcast_shapes((2, 3), (2, 3, 1)))


def test_elemwise_on_scalars():
    x = np.arange(10)
    a = from_array(x, chunks=(5,))
    assert len(a._keys()) == 2
    assert eq(a.sum()**2, x.sum()**2)

    x = np.arange(11)
    a = from_array(x, chunks=(5,))
    assert len(a._keys()) == 3
    assert eq(a, x)


def test_partial_by_order():
    f = partial_by_order(add, [(1, 20)])
    assert f(5) == 25
    assert f.__name__ == 'add(20)'

    f = partial_by_order(lambda x, y, z: x + y + z, [(1, 10), (2, 15)])
    assert f(3) == 28
    assert f.__name__ == '<lambda>(...)'

    assert raises(ValueError, lambda: partial_by_order(add, 1))
    assert raises(ValueError, lambda: partial_by_order(add, [1]))


def test_elemwise_with_ndarrays():
    x = np.arange(3)
    y = np.arange(12).reshape(4, 3)
    a = from_array(x, chunks=(3,))
    b = from_array(y, chunks=(2, 3))

    assert eq(x + a, 2 * x)
    assert eq(a + x, 2 * x)

    assert eq(x + b, x + y)
    assert eq(b + x, x + y)
    assert eq(a + y, x + y)
    assert eq(y + a, x + y)
    # Error on shape mismatch
    assert raises(ValueError, lambda: a + y.T)
    assert raises(ValueError, lambda: a + np.arange(2))


def test_elemwise_differently_chunked():
    x = np.arange(3)
    y = np.arange(12).reshape(4, 3)
    a = from_array(x, chunks=(3,))
    b = from_array(y, chunks=(2, 2))

    assert eq(a + b, x + y)
    assert eq(b + a, x + y)


def test_operators():
    x = np.arange(10)
    y = np.arange(10).reshape((10, 1))
    a = from_array(x, chunks=(5,))
    b = from_array(y, chunks=(5, 1))

    c = a + 1
    assert eq(c, x + 1)

    c = a + b
    assert eq(c, x + x.reshape((10, 1)))

    expr = (3 / a * b)**2 > 5
    assert eq(expr, (3 / x * y)**2 > 5)

    c = exp(a)
    assert eq(c, np.exp(x))

    assert eq(abs(-a), a)
    assert eq(a, +x)


def test_operator_dtype_promotion():
    x = np.arange(10, dtype=np.float32)
    y = np.array([1])
    a = from_array(x, chunks=(5,))

    assert eq(x + 1, a + 1)  # still float32
    assert eq(x + 1e50, a + 1e50)  # now float64
    assert eq(x + y, a + y)  # also float64


def test_field_access():
    x = np.array([(1, 1.0), (2, 2.0)], dtype=[('a', 'i4'), ('b', 'f4')])
    y = from_array(x, chunks=(1,))
    assert eq(y['a'], x['a'])
    assert eq(y[['b', 'a']], x[['b', 'a']])
    assert same_keys(y[['b', 'a']], y[['b', 'a']])


def test_tensordot():
    x = np.arange(400).reshape((20, 20))
    a = from_array(x, chunks=(5, 5))
    y = np.arange(200).reshape((20, 10))
    b = from_array(y, chunks=(5, 5))

    assert eq(tensordot(a, b, axes=1), np.tensordot(x, y, axes=1))
    assert eq(tensordot(a, b, axes=(1, 0)), np.tensordot(x, y, axes=(1, 0)))
    assert same_keys(tensordot(a, b, axes=(1, 0)), tensordot(a, b, axes=(1, 0)))
    assert not same_keys(tensordot(a, b, axes=0), tensordot(a, b, axes=1))

    # assert (tensordot(a, a).chunks
    #      == tensordot(a, a, axes=((1, 0), (0, 1))).chunks)

    # assert eq(tensordot(a, a), np.tensordot(x, x))


def test_dot_method():
    x = np.arange(400).reshape((20, 20))
    a = from_array(x, chunks=(5, 5))
    y = np.arange(200).reshape((20, 10))
    b = from_array(y, chunks=(5, 5))

    assert eq(a.dot(b), x.dot(y))


def test_T():
    x = np.arange(400).reshape((20, 20))
    a = from_array(x, chunks=(5, 5))

    assert eq(x.T, a.T)


def test_norm():
    a = np.arange(200, dtype='f8').reshape((20, 10))
    b = from_array(a, chunks=(5, 5))

    assert eq(b.vnorm(), np.linalg.norm(a))
    assert eq(b.vnorm(ord=1), np.linalg.norm(a.flatten(), ord=1))
    assert eq(b.vnorm(ord=4, axis=0), np.linalg.norm(a, ord=4, axis=0))
    assert b.vnorm(ord=4, axis=0, keepdims=True).ndim == b.ndim
    split_every = {0: 3, 1: 3}
    assert eq(b.vnorm(ord=1, axis=0, split_every=split_every),
              np.linalg.norm(a, ord=1, axis=0))
    assert eq(b.vnorm(ord=np.inf, axis=0, split_every=split_every),
              np.linalg.norm(a, ord=np.inf, axis=0))
    assert eq(b.vnorm(ord=np.inf, split_every=split_every),
              np.linalg.norm(a.flatten(), ord=np.inf))


def test_choose():
    x = np.random.randint(10, size=(15, 16))
    d = from_array(x, chunks=(4, 5))

    assert eq(choose(d > 5, [0, d]), np.choose(x > 5, [0, x]))
    assert eq(choose(d > 5, [-d, d]), np.choose(x > 5, [-x, x]))


def test_where():
    x = np.random.randint(10, size=(15, 16))
    d = from_array(x, chunks=(4, 5))
    y = np.random.randint(10, size=15)
    e = from_array(y, chunks=(4,))

    assert eq(where(d > 5, d, 0), np.where(x > 5, x, 0))
    assert eq(where(d > 5, d, -e[:, None]), np.where(x > 5, x, -y[:, None]))


def test_where_has_informative_error():
    x = da.ones(5, chunks=3)
    try:
        result = da.where(x > 0)
    except Exception as e:
        assert 'dask' in str(e)


def test_coarsen():
    x = np.random.randint(10, size=(24, 24))
    d = from_array(x, chunks=(4, 8))

    assert eq(chunk.coarsen(np.sum, x, {0: 2, 1: 4}),
                    coarsen(np.sum, d, {0: 2, 1: 4}))
    assert eq(chunk.coarsen(np.sum, x, {0: 2, 1: 4}),
                    coarsen(da.sum, d, {0: 2, 1: 4}))


def test_coarsen_with_excess():
    x = da.arange(10, chunks=5)
    assert eq(coarsen(np.min, x, {0: 3}, trim_excess=True),
              np.array([0, 5]))
    assert eq(coarsen(np.sum, x, {0: 3}, trim_excess=True),
              np.array([0+1+2, 5+6+7]))


def test_insert():
    x = np.random.randint(10, size=(10, 10))
    a = from_array(x, chunks=(5, 5))
    y = np.random.randint(10, size=(5, 10))
    b = from_array(y, chunks=(4, 4))

    assert eq(np.insert(x, 0, -1, axis=0), insert(a, 0, -1, axis=0))
    assert eq(np.insert(x, 3, -1, axis=-1), insert(a, 3, -1, axis=-1))
    assert eq(np.insert(x, 5, -1, axis=1), insert(a, 5, -1, axis=1))
    assert eq(np.insert(x, -1, -1, axis=-2), insert(a, -1, -1, axis=-2))
    assert eq(np.insert(x, [2, 3, 3], -1, axis=1),
                 insert(a, [2, 3, 3], -1, axis=1))
    assert eq(np.insert(x, [2, 3, 8, 8, -2, -2], -1, axis=0),
                 insert(a, [2, 3, 8, 8, -2, -2], -1, axis=0))
    assert eq(np.insert(x, slice(1, 4), -1, axis=1),
                 insert(a, slice(1, 4), -1, axis=1))
    assert eq(np.insert(x, [2] * 3 + [5] * 2, y, axis=0),
                 insert(a, [2] * 3 + [5] * 2, b, axis=0))
    assert eq(np.insert(x, 0, y[0], axis=1),
                 insert(a, 0, b[0], axis=1))
    assert raises(NotImplementedError, lambda: insert(a, [4, 2], -1, axis=0))
    assert raises(IndexError, lambda: insert(a, [3], -1, axis=2))
    assert raises(IndexError, lambda: insert(a, [3], -1, axis=-3))
    assert same_keys(insert(a, [2, 3, 8, 8, -2, -2], -1, axis=0),
                    insert(a, [2, 3, 8, 8, -2, -2], -1, axis=0))


def test_multi_insert():
    z = np.random.randint(10, size=(1, 2))
    c = from_array(z, chunks=(1, 2))
    assert eq(np.insert(np.insert(z, [0, 1], -1, axis=0), [1], -1, axis=1),
              insert(insert(c, [0, 1], -1, axis=0), [1], -1, axis=1))


def test_broadcast_to():
    x = np.random.randint(10, size=(5, 1, 6))
    a = from_array(x, chunks=(3, 1, 3))

    for shape in [(5, 4, 6), (2, 5, 1, 6), (3, 4, 5, 4, 6)]:
        assert eq(chunk.broadcast_to(x, shape),
                        broadcast_to(a, shape))

    assert raises(ValueError, lambda: broadcast_to(a, (2, 1, 6)))
    assert raises(ValueError, lambda: broadcast_to(a, (3,)))


def test_ravel():
    x = np.random.randint(10, size=(4, 6))

    # 2d
    # these should use the shortcut
    for chunks in [(4, 6), (2, 6)]:
        a = from_array(x, chunks=chunks)
        assert eq(x.ravel(), a.ravel())
        assert len(a.ravel().dask) == len(a.dask) + len(a.chunks[0])
    # these cannot
    for chunks in [(4, 2), (2, 2)]:
        a = from_array(x, chunks=chunks)
        assert eq(x.ravel(), a.ravel())
        assert len(a.ravel().dask) > len(a.dask) + len(a.chunks[0])

    # 0d
    assert eq(x[0, 0].ravel(), a[0, 0].ravel())

    # 1d
    a_flat = a.ravel()
    assert a_flat.ravel() is a_flat

    # 3d
    x = np.random.randint(10, size=(2, 3, 4))
    for chunks in [2, 4, (2, 3, 2), (1, 3, 4)]:
        a = from_array(x, chunks=chunks)
        assert eq(x.ravel(), a.ravel())

    assert eq(x.flatten(), a.flatten())
    assert eq(np.ravel(x), da.ravel(a))


def test_unravel():
    x = np.random.randint(10, size=24)

    # these should use the shortcut
    for chunks, shape in [(24, (3, 8)),
                          (24, (12, 2)),
                          (6, (4, 6)),
                          (6, (4, 3, 2)),
                          (6, (4, 6, 1)),
                          (((6, 12, 6),), (4, 6))]:
        a = from_array(x, chunks=chunks)
        unraveled = unravel(a, shape)
        assert eq(x.reshape(*shape), unraveled)
        assert len(unraveled.dask) == len(a.dask) + len(a.chunks[0])

    # these cannot
    for chunks, shape in [(6, (2, 12)),
                          (6, (1, 4, 6)),
                          (6, (2, 1, 12))]:
        a = from_array(x, chunks=chunks)
        unraveled = unravel(a, shape)
        assert eq(x.reshape(*shape), unraveled)
        assert len(unraveled.dask) > len(a.dask) + len(a.chunks[0])

    assert raises(AssertionError, lambda: unravel(unraveled, (3, 8)))
    assert unravel(a, a.shape) is a


def test_reshape():
    shapes = [(24,), (2, 12), (2, 3, 4)]
    for original_shape in shapes:
        for new_shape in shapes:
            for chunks in [2, 4, 12]:
                x = np.random.randint(10, size=original_shape)
                a = from_array(x, chunks)
                assert eq(x.reshape(new_shape), a.reshape(new_shape))

    assert raises(ValueError, lambda: reshape(a, (100,)))
    assert eq(x.reshape(*new_shape), a.reshape(*new_shape))
    assert eq(np.reshape(x, new_shape), reshape(a, new_shape))

    # verify we can reshape a single chunk array without too many tasks
    x = np.random.randint(10, size=(10, 20))
    a = from_array(x, 20)  # all one chunk
    reshaped = a.reshape((20, 10))
    assert eq(x.reshape((20, 10)), reshaped)
    assert len(reshaped.dask) == len(a.dask) + 2


def test_reshape_unknown_dimensions():
    for original_shape in [(24,), (2, 12), (2, 3, 4)]:
        for new_shape in [(-1,), (2, -1), (-1, 3, 4)]:
            x = np.random.randint(10, size=original_shape)
            a = from_array(x, 4)
            assert eq(x.reshape(new_shape), a.reshape(new_shape))

    assert raises(ValueError, lambda: reshape(a, (-1, -1)))


def test_full():
    d = da.full((3, 4), 2, chunks=((2, 1), (2, 2)))
    assert d.chunks == ((2, 1), (2, 2))
    assert eq(d, np.full((3, 4), 2))


def test_map_blocks():
    inc = lambda x: x + 1

    x = np.arange(400).reshape((20, 20))
    d = from_array(x, chunks=(7, 7))

    e = d.map_blocks(inc, dtype=d.dtype)

    assert d.chunks == e.chunks
    assert eq(e, x + 1)

    e = d.map_blocks(inc, name='increment')
    assert e.name == 'increment'

    d = from_array(x, chunks=(10, 10))
    e = d.map_blocks(lambda x: x[::2, ::2], chunks=(5, 5), dtype=d.dtype)

    assert e.chunks == ((5, 5), (5, 5))
    assert eq(e, x[::2, ::2])

    d = from_array(x, chunks=(8, 8))
    e = d.map_blocks(lambda x: x[::2, ::2], chunks=((4, 4, 2), (4, 4, 2)),
            dtype=d.dtype)

    assert eq(e, x[::2, ::2])


def test_map_blocks2():
    x = np.arange(10, dtype='i8')
    d = from_array(x, chunks=(2,))

    def func(block, block_id=None):
        return np.ones_like(block) * sum(block_id)

    out = d.map_blocks(func, dtype='i8')
    expected = np.array([0, 0, 1, 1, 2, 2, 3, 3, 4, 4], dtype='i8')

    assert eq(out, expected)
    assert same_keys(d.map_blocks(func, dtype='i8'), out)


def test_map_blocks_with_constants():
    d = da.arange(10, chunks=3)
    e = d.map_blocks(add, 100, dtype=d.dtype)

    assert eq(e, np.arange(10) + 100)

    assert eq(da.map_blocks(sub, d, 10, dtype=d.dtype),
              np.arange(10) - 10)
    assert eq(da.map_blocks(sub, 10, d, dtype=d.dtype),
              10 - np.arange(10))


def test_map_blocks_with_kwargs():
    d = da.arange(10, chunks=5)

    assert eq(d.map_blocks(np.max, axis=0, keepdims=True, dtype=d.dtype),
              np.array([4, 9]))


def test_fromfunction():
    def f(x, y):
        return x + y
    d = fromfunction(f, shape=(5, 5), chunks=(2, 2), dtype='f8')

    assert eq(d, np.fromfunction(f, shape=(5, 5)))
    assert same_keys(d, fromfunction(f, shape=(5, 5), chunks=(2, 2), dtype='f8'))


def test_from_function_requires_block_args():
    x = np.arange(10)
    assert raises(Exception, lambda: from_array(x))


def test_repr():
    d = da.ones((4, 4), chunks=(2, 2))
    assert d.name[:5] in repr(d)
    assert str(d.shape) in repr(d)
    assert str(d._dtype) in repr(d)
    d = da.ones((4000, 4), chunks=(4, 2))
    assert len(str(d)) < 1000


def test_slicing_with_ellipsis():
    x = np.arange(256).reshape((4, 4, 4, 4))
    d = da.from_array(x, chunks=((2, 2, 2, 2)))

    assert eq(d[..., 1], x[..., 1])
    assert eq(d[0, ..., 1], x[0, ..., 1])


def test_slicing_with_ndarray():
    x = np.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=((4, 4)))

    assert eq(d[np.arange(8)], x)
    assert eq(d[np.ones(8, dtype=bool)], x)


def test_dtype():
    d = da.ones((4, 4), chunks=(2, 2))

    assert d.dtype == d.compute().dtype
    assert (d * 1.0).dtype == (d + 1.0).compute().dtype
    assert d.sum().dtype == d.sum().compute().dtype  # no shape


def test_blockdims_from_blockshape():
    assert blockdims_from_blockshape((10, 10), (4, 3)) == ((4, 4, 2), (3, 3, 3, 1))
    assert raises(TypeError, lambda: blockdims_from_blockshape((10,), None))
    assert blockdims_from_blockshape((1e2, 3), [1e1, 3]) == ((10,)*10, (3,))
    assert blockdims_from_blockshape((np.int8(10),), (5,)) == ((5, 5),)


def test_coerce():
    d = da.from_array(np.array([1]), chunks=(1,))
    with dask.set_options(get=dask.get):
        assert bool(d)
        assert int(d)
        assert float(d)
        assert complex(d)


def test_store():
    d = da.ones((4, 4), chunks=(2, 2))
    a, b = d + 1, d + 2

    at = np.empty(shape=(4, 4))
    bt = np.empty(shape=(4, 4))

    store([a, b], [at, bt])
    assert (at == 2).all()
    assert (bt == 3).all()

    assert raises(ValueError, lambda: store([a], [at, bt]))
    assert raises(ValueError, lambda: store(at, at))
    assert raises(ValueError, lambda: store([at, bt], [at, bt]))


def test_to_hdf5():
    try:
        import h5py
    except ImportError:
        return
    x = da.ones((4, 4), chunks=(2, 2))
    y = da.ones(4, chunks=2, dtype='i4')

    with tmpfile('.hdf5') as fn:
        x.to_hdf5(fn, '/x')
        with h5py.File(fn) as f:
            d = f['/x']

            assert eq(d[:], x)
            assert d.chunks == (2, 2)

    with tmpfile('.hdf5') as fn:
        x.to_hdf5(fn, '/x', chunks=None)
        with h5py.File(fn) as f:
            d = f['/x']

            assert eq(d[:], x)
            assert d.chunks is None

    with tmpfile('.hdf5') as fn:
        x.to_hdf5(fn, '/x', chunks=(1, 1))
        with h5py.File(fn) as f:
            d = f['/x']

            assert eq(d[:], x)
            assert d.chunks == (1, 1)

    with tmpfile('.hdf5') as fn:
        da.to_hdf5(fn, {'/x': x, '/y': y})

        with h5py.File(fn) as f:
            assert eq(f['/x'][:], x)
            assert f['/x'].chunks == (2, 2)
            assert eq(f['/y'][:], y)
            assert f['/y'].chunks == (2,)


def test_np_array_with_zero_dimensions():
    d = da.ones((4, 4), chunks=(2, 2))
    assert eq(np.array(d.sum()), np.array(d.compute().sum()))


def test_unique():
    x = np.array([1, 2, 4, 4, 5, 2])
    d = da.from_array(x, chunks=(3,))
    assert eq(da.unique(d), np.unique(x))


def test_dtype_complex():
    x = np.arange(24).reshape((4, 6)).astype('f4')
    y = np.arange(24).reshape((4, 6)).astype('i8')
    z = np.arange(24).reshape((4, 6)).astype('i2')

    a = da.from_array(x, chunks=(2, 3))
    b = da.from_array(y, chunks=(2, 3))
    c = da.from_array(z, chunks=(2, 3))

    def eq(a, b):
        return (isinstance(a, np.dtype) and
                isinstance(b, np.dtype) and
                str(a) == str(b))

    assert eq(a._dtype, x.dtype)
    assert eq(b._dtype, y.dtype)

    assert eq((a + 1)._dtype, (x + 1).dtype)
    assert eq((a + b)._dtype, (x + y).dtype)
    assert eq(a.T._dtype, x.T.dtype)
    assert eq(a[:3]._dtype, x[:3].dtype)
    assert eq((a.dot(b.T))._dtype, (x.dot(y.T)).dtype)

    assert eq(stack([a, b])._dtype, np.vstack([x, y]).dtype)
    assert eq(concatenate([a, b])._dtype, np.concatenate([x, y]).dtype)

    assert eq(b.std()._dtype, y.std().dtype)
    assert eq(c.sum()._dtype, z.sum().dtype)
    assert eq(a.min()._dtype, a.min().dtype)
    assert eq(b.std()._dtype, b.std().dtype)
    assert eq(a.argmin(axis=0)._dtype, a.argmin(axis=0).dtype)

    assert eq(da.sin(c)._dtype, np.sin(z).dtype)
    assert eq(da.exp(b)._dtype, np.exp(y).dtype)
    assert eq(da.floor(a)._dtype, np.floor(x).dtype)
    assert eq(da.isnan(b)._dtype, np.isnan(y).dtype)
    with ignoring(ImportError):
        assert da.isnull(b)._dtype == 'bool'
        assert da.notnull(b)._dtype == 'bool'

    x = np.array([('a', 1)], dtype=[('text', 'S1'), ('numbers', 'i4')])
    d = da.from_array(x, chunks=(1,))

    assert eq(d['text']._dtype, x['text'].dtype)
    assert eq(d[['numbers', 'text']]._dtype, x[['numbers', 'text']].dtype)


def test_astype():
    x = np.ones(5, dtype='f4')
    d = da.from_array(x, chunks=(2,))

    assert d.astype('i8')._dtype == 'i8'
    assert eq(d.astype('i8'), x.astype('i8'))
    assert same_keys(d.astype('i8'), d.astype('i8'))


def test_arithmetic():
    x = np.arange(5).astype('f4') + 2
    y = np.arange(5).astype('i8') + 2
    z = np.arange(5).astype('i4') + 2
    a = da.from_array(x, chunks=(2,))
    b = da.from_array(y, chunks=(2,))
    c = da.from_array(z, chunks=(2,))
    assert eq(a + b, x + y)
    assert eq(a * b, x * y)
    assert eq(a - b, x - y)
    assert eq(a / b, x / y)
    assert eq(b & b, y & y)
    assert eq(b | b, y | y)
    assert eq(b ^ b, y ^ y)
    assert eq(a // b, x // y)
    assert eq(a ** b, x ** y)
    assert eq(a % b, x % y)
    assert eq(a > b, x > y)
    assert eq(a < b, x < y)
    assert eq(a >= b, x >= y)
    assert eq(a <= b, x <= y)
    assert eq(a == b, x == y)
    assert eq(a != b, x != y)

    assert eq(a + 2, x + 2)
    assert eq(a * 2, x * 2)
    assert eq(a - 2, x - 2)
    assert eq(a / 2, x / 2)
    assert eq(b & True, y & True)
    assert eq(b | True, y | True)
    assert eq(b ^ True, y ^ True)
    assert eq(a // 2, x // 2)
    assert eq(a ** 2, x ** 2)
    assert eq(a % 2, x % 2)
    assert eq(a > 2, x > 2)
    assert eq(a < 2, x < 2)
    assert eq(a >= 2, x >= 2)
    assert eq(a <= 2, x <= 2)
    assert eq(a == 2, x == 2)
    assert eq(a != 2, x != 2)

    assert eq(2 + b, 2 + y)
    assert eq(2 * b, 2 * y)
    assert eq(2 - b, 2 - y)
    assert eq(2 / b, 2 / y)
    assert eq(True & b, True & y)
    assert eq(True | b, True | y)
    assert eq(True ^ b, True ^ y)
    assert eq(2 // b, 2 // y)
    assert eq(2 ** b, 2 ** y)
    assert eq(2 % b, 2 % y)
    assert eq(2 > b, 2 > y)
    assert eq(2 < b, 2 < y)
    assert eq(2 >= b, 2 >= y)
    assert eq(2 <= b, 2 <= y)
    assert eq(2 == b, 2 == y)
    assert eq(2 != b, 2 != y)

    assert eq(-a, -x)
    assert eq(abs(a), abs(x))
    assert eq(~(a == b), ~(x == y))
    assert eq(~(a == b), ~(x == y))

    assert eq(da.logaddexp(a, b), np.logaddexp(x, y))
    assert eq(da.logaddexp2(a, b), np.logaddexp2(x, y))
    assert eq(da.exp(b), np.exp(y))
    assert eq(da.log(a), np.log(x))
    assert eq(da.log10(a), np.log10(x))
    assert eq(da.log1p(a), np.log1p(x))
    assert eq(da.expm1(b), np.expm1(y))
    assert eq(da.sqrt(a), np.sqrt(x))
    assert eq(da.square(a), np.square(x))

    assert eq(da.sin(a), np.sin(x))
    assert eq(da.cos(b), np.cos(y))
    assert eq(da.tan(a), np.tan(x))
    assert eq(da.arcsin(b/10), np.arcsin(y/10))
    assert eq(da.arccos(b/10), np.arccos(y/10))
    assert eq(da.arctan(b/10), np.arctan(y/10))
    assert eq(da.arctan2(b*10, a), np.arctan2(y*10, x))
    assert eq(da.hypot(b, a), np.hypot(y, x))
    assert eq(da.sinh(a), np.sinh(x))
    assert eq(da.cosh(b), np.cosh(y))
    assert eq(da.tanh(a), np.tanh(x))
    assert eq(da.arcsinh(b*10), np.arcsinh(y*10))
    assert eq(da.arccosh(b*10), np.arccosh(y*10))
    assert eq(da.arctanh(b/10), np.arctanh(y/10))
    assert eq(da.deg2rad(a), np.deg2rad(x))
    assert eq(da.rad2deg(a), np.rad2deg(x))

    assert eq(da.logical_and(a < 1, b < 4), np.logical_and(x < 1, y < 4))
    assert eq(da.logical_or(a < 1, b < 4), np.logical_or(x < 1, y < 4))
    assert eq(da.logical_xor(a < 1, b < 4), np.logical_xor(x < 1, y < 4))
    assert eq(da.logical_not(a < 1), np.logical_not(x < 1))
    assert eq(da.maximum(a, 5 - a), np.maximum(a, 5 - a))
    assert eq(da.minimum(a, 5 - a), np.minimum(a, 5 - a))
    assert eq(da.fmax(a, 5 - a), np.fmax(a, 5 - a))
    assert eq(da.fmin(a, 5 - a), np.fmin(a, 5 - a))

    assert eq(da.isreal(a + 1j * b), np.isreal(x + 1j * y))
    assert eq(da.iscomplex(a + 1j * b), np.iscomplex(x + 1j * y))
    assert eq(da.isfinite(a), np.isfinite(x))
    assert eq(da.isinf(a), np.isinf(x))
    assert eq(da.isnan(a), np.isnan(x))
    assert eq(da.signbit(a - 3), np.signbit(x - 3))
    assert eq(da.copysign(a - 3, b), np.copysign(x - 3, y))
    assert eq(da.nextafter(a - 3, b), np.nextafter(x - 3, y))
    assert eq(da.ldexp(c, c), np.ldexp(z, z))
    assert eq(da.fmod(a * 12, b), np.fmod(x * 12, y))
    assert eq(da.floor(a * 0.5), np.floor(x * 0.5))
    assert eq(da.ceil(a), np.ceil(x))
    assert eq(da.trunc(a / 2), np.trunc(x / 2))

    assert eq(da.degrees(b), np.degrees(y))
    assert eq(da.radians(a), np.radians(x))

    assert eq(da.rint(a + 0.3), np.rint(x + 0.3))
    assert eq(da.fix(a - 2.5), np.fix(x - 2.5))

    assert eq(da.angle(a + 1j), np.angle(x + 1j))
    assert eq(da.real(a + 1j), np.real(x + 1j))
    assert eq((a + 1j).real, np.real(x + 1j))
    assert eq(da.imag(a + 1j), np.imag(x + 1j))
    assert eq((a + 1j).imag, np.imag(x + 1j))
    assert eq(da.conj(a + 1j * b), np.conj(x + 1j * y))
    assert eq((a + 1j * b).conj(), (x + 1j * y).conj())

    assert eq(da.clip(b, 1, 4), np.clip(y, 1, 4))
    assert eq(da.fabs(b), np.fabs(y))
    assert eq(da.sign(b - 2), np.sign(y - 2))

    l1, l2 = da.frexp(a)
    r1, r2 = np.frexp(x)
    assert eq(l1, r1)
    assert eq(l2, r2)

    l1, l2 = da.modf(a)
    r1, r2 = np.modf(x)
    assert eq(l1, r1)
    assert eq(l2, r2)

    assert eq(da.around(a, -1), np.around(x, -1))


def test_elemwise_consistent_names():
    a = da.from_array(np.arange(5, dtype='f4'), chunks=(2,))
    b = da.from_array(np.arange(5, dtype='f4'), chunks=(2,))
    assert same_keys(a + b, a + b)
    assert same_keys(a + 2, a + 2)
    assert same_keys(da.exp(a), da.exp(a))
    assert same_keys(da.exp(a, dtype='f8'), da.exp(a, dtype='f8'))
    assert same_keys(da.maximum(a, b), da.maximum(a, b))


def test_optimize():
    x = np.arange(5).astype('f4')
    a = da.from_array(x, chunks=(2,))
    expr = a[1:4] + 1
    result = optimize(expr.dask, expr._keys())
    assert isinstance(result, dict)
    assert all(key in result for key in expr._keys())


def test_slicing_with_non_ndarrays():
    class ARangeSlice(object):
        def __init__(self, start, stop):
            self.start = start
            self.stop = stop

        def __array__(self):
            return np.arange(self.start, self.stop)

    class ARangeSlicable(object):
        dtype = 'i8'

        def __init__(self, n):
            self.n = n

        @property
        def shape(self):
            return (self.n,)

        def __getitem__(self, key):
            return ARangeSlice(key[0].start, key[0].stop)


    x = da.from_array(ARangeSlicable(10), chunks=(4,))

    assert eq((x + 1).sum(), (np.arange(10, dtype=x.dtype) + 1).sum())


def test_getarray():
    assert type(getarray(np.matrix([[1]]), 0)) == np.ndarray
    assert eq(getarray([1, 2, 3, 4, 5], slice(1, 4)), np.array([2, 3, 4]))

    assert eq(getarray(np.arange(5), (None, slice(None, None))),
              np.arange(5)[None, :])


def test_squeeze():
    x = da.ones((10, 1), chunks=(3, 1))

    assert eq(x.squeeze(), x.compute().squeeze())

    assert x.squeeze().chunks == ((3, 3, 3, 1),)
    assert same_keys(x.squeeze(), x.squeeze())


def test_size():
    x = da.ones((10, 2), chunks=(3, 1))
    assert x.size == np.array(x).size


def test_nbytes():
    x = da.ones((10, 2), chunks=(3, 1))
    assert x.nbytes == np.array(x).nbytes


def test_Array_normalizes_dtype():
    x = da.ones((3,), chunks=(1,), dtype=int)
    assert isinstance(x.dtype, np.dtype)


def test_args():
    x = da.ones((10, 2), chunks=(3, 1), dtype='i4') + 1
    y = Array(*x._args)
    assert eq(x, y)


def test_from_array_with_lock():
    x = np.arange(10)
    d = da.from_array(x, chunks=5, lock=True)

    tasks = [v for k, v in d.dask.items() if k[0] == d.name]

    assert isinstance(tasks[0][3], type(Lock()))
    assert len(set(task[3] for task in tasks)) == 1

    assert eq(d, x)

    lock = Lock()
    e = da.from_array(x, chunks=5, lock=lock)
    f = da.from_array(x, chunks=5, lock=lock)

    assert eq(e + f, x + x)


def test_from_func():
    x = np.arange(10)
    f = lambda n: n * x
    d = from_func(f, (10,), x.dtype, kwargs={'n': 2})

    assert d.shape == x.shape
    assert d.dtype == x.dtype
    assert eq(d.compute(), 2 * x)
    assert same_keys(d, from_func(f, (10,), x.dtype, kwargs={'n': 2}))


def test_topk():
    x = np.array([5, 2, 1, 6])
    d = da.from_array(x, chunks=2)

    e = da.topk(2, d)

    assert e.chunks == ((2,),)
    assert eq(e, np.sort(x)[-1:-3:-1])
    assert same_keys(da.topk(2, d), e)


def test_topk_k_bigger_than_chunk():
    x = np.array([5, 2, 1, 6])
    d = da.from_array(x, chunks=2)

    e = da.topk(3, d)

    assert e.chunks == ((3,),)
    assert eq(e, np.array([6, 5, 2]))


def test_bincount():
    x = np.array([2, 1, 5, 2, 1])
    d = da.from_array(x, chunks=2)
    e = da.bincount(d, minlength=6)
    assert eq(e, np.bincount(x, minlength=6))
    assert same_keys(da.bincount(d, minlength=6), e)


def test_bincount_with_weights():
    x = np.array([2, 1, 5, 2, 1])
    d = da.from_array(x, chunks=2)
    weights = np.array([1, 2, 1, 0.5, 1])

    dweights = da.from_array(weights, chunks=2)
    e = da.bincount(d, weights=dweights, minlength=6)
    assert eq(e, np.bincount(x, weights=dweights, minlength=6))
    assert same_keys(da.bincount(d, weights=dweights, minlength=6), e)


def test_bincount_raises_informative_error_on_missing_minlength_kwarg():
    x = np.array([2, 1, 5, 2, 1])
    d = da.from_array(x, chunks=2)
    try:
        da.bincount(d)
    except Exception as e:
        assert 'minlength' in str(e)
    else:
        assert False


def test_histogram():
    # Test for normal, flattened input
    n = 100
    v = da.random.random(n, chunks=10)
    bins = np.arange(0, 1.01, 0.01)
    (a1, b1) = da.histogram(v, bins=bins)
    (a2, b2) = np.histogram(v, bins=bins)

    # Check if the sum of the bins equals the number of samples
    assert a2.sum(axis=0) == n
    assert a1.sum(axis=0) == n
    assert eq(a1, a2)
    assert same_keys(da.histogram(v, bins=bins)[0], a1)


def test_histogram_alternative_bins_range():
    v = da.random.random(100, chunks=10)
    bins = np.arange(0, 1.01, 0.01)
    # Other input
    (a1, b1) = da.histogram(v, bins=10, range=(0, 1))
    (a2, b2) = np.histogram(v, bins=10, range=(0, 1))
    assert eq(a1, a2)
    assert eq(b1, b2)


def test_histogram_return_type():
    v = da.random.random(100, chunks=10)
    bins = np.arange(0, 1.01, 0.01)
    # Check if return type is same as hist
    bins = np.arange(0, 11, 1, dtype='i4')
    assert eq(da.histogram(v * 10, bins=bins)[0],
              np.histogram(v * 10, bins=bins)[0])


def test_histogram_extra_args_and_shapes():
    # Check for extra args and shapes
    bins = np.arange(0, 1.01, 0.01)
    v = da.random.random(100, chunks=10)
    data = [(v, bins, da.ones(100, chunks=v.chunks) * 5),
            (da.random.random((50, 50), chunks=10), bins, da.ones((50, 50), chunks=10) * 5)]

    for v, bins, w in data:
        # density
        assert eq(da.histogram(v, bins=bins, normed=True)[0],
                  np.histogram(v, bins=bins, normed=True)[0])

        # normed
        assert eq(da.histogram(v, bins=bins, density=True)[0],
                  np.histogram(v, bins=bins, density=True)[0])

        # weights
        assert eq(da.histogram(v, bins=bins, weights=w)[0],
                  np.histogram(v, bins=bins, weights=w)[0])

        assert eq(da.histogram(v, bins=bins, weights=w, density=True)[0],
                  da.histogram(v, bins=bins, weights=w, density=True)[0])


def test_concatenate3():
    x = np.array([1, 2])
    assert eq(concatenate3([x, x, x]),
              np.array([1, 2, 1, 2, 1, 2]))

    x = np.array([[1, 2]])
    assert (concatenate3([[x, x, x], [x, x, x]]) ==
            np.array([[1, 2, 1, 2, 1, 2],
                      [1, 2, 1, 2, 1, 2]])).all()

    assert (concatenate3([[x, x], [x, x], [x, x]]) ==
            np.array([[1, 2, 1, 2],
                      [1, 2, 1, 2],
                      [1, 2, 1, 2]])).all()

    x = np.arange(12).reshape((2, 2, 3))
    assert eq(concatenate3([[[x, x, x],
                             [x, x, x]],
                            [[x, x, x],
                             [x, x, x]]]),
              np.array([[[ 0,  1,  2,  0,  1,  2,  0,  1,  2],
                         [ 3,  4,  5,  3,  4,  5,  3,  4,  5],
                         [ 0,  1,  2,  0,  1,  2,  0,  1,  2],
                         [ 3,  4,  5,  3,  4,  5,  3,  4,  5]],

                        [[ 6,  7,  8,  6,  7,  8,  6,  7,  8],
                         [ 9, 10, 11,  9, 10, 11,  9, 10, 11],
                         [ 6,  7,  8,  6,  7,  8,  6,  7,  8],
                         [ 9, 10, 11,  9, 10, 11,  9, 10, 11]],

                        [[ 0,  1,  2,  0,  1,  2,  0,  1,  2],
                         [ 3,  4,  5,  3,  4,  5,  3,  4,  5],
                         [ 0,  1,  2,  0,  1,  2,  0,  1,  2],
                         [ 3,  4,  5,  3,  4,  5,  3,  4,  5]],

                        [[ 6,  7,  8,  6,  7,  8,  6,  7,  8],
                         [ 9, 10, 11,  9, 10, 11,  9, 10, 11],
                         [ 6,  7,  8,  6,  7,  8,  6,  7,  8],
                         [ 9, 10, 11,  9, 10, 11,  9, 10, 11]]]))


def test_map_blocks3():
    x = np.arange(10)
    y = np.arange(10) * 2

    d = da.from_array(x, chunks=5)
    e = da.from_array(y, chunks=5)

    assert eq(da.core.map_blocks(lambda a, b: a+2*b, d, e, dtype=d.dtype),
              x + 2*y)

    z = np.arange(100).reshape((10, 10))
    f = da.from_array(z, chunks=5)

    func = lambda a, b: a + 2*b
    res = da.core.map_blocks(func, d, f, dtype=d.dtype)
    assert eq(res, x + 2*z)
    assert same_keys(da.core.map_blocks(func, d, f, dtype=d.dtype), res)

    assert eq(da.map_blocks(func, f, d, dtype=d.dtype),
              z + 2*x)


def test_from_array_with_missing_chunks():
    x = np.random.randn(2, 4, 3)
    d = da.from_array(x, chunks=(None, 2, None))
    assert d.chunks == da.from_array(x, chunks=(2, 2, 3)).chunks


def test_cache():
    x = da.arange(15, chunks=5)
    y = 2 * x + 1
    z = y.cache()
    assert len(z.dask) == 3  # very short graph
    assert eq(y, z)

    cache = np.empty(15, dtype=y.dtype)
    z = y.cache(store=cache)
    assert len(z.dask) < 6  # very short graph
    assert z.chunks == y.chunks
    assert eq(y, z)


def test_take_dask_from_numpy():
    x = np.arange(5).astype('f8')
    y = da.from_array(np.array([1, 2, 3, 3, 2 ,1]), chunks=3)

    z = da.take(x * 2, y)

    assert z.chunks == y.chunks
    assert eq(z, np.array([2., 4., 6., 6., 4., 2.]))


def test_normalize_chunks():
    assert normalize_chunks(3, (4, 6)) == ((3, 1), (3, 3))


def test_raise_on_no_chunks():
    x = da.ones(6, chunks=3)
    try:
        Array(x.dask, x.name, chunks=None, dtype=x.dtype, shape=None)
        assert False
    except ValueError as e:
        assert "dask.pydata.org" in str(e)

    assert raises(ValueError, lambda: da.ones(6))


def test_chunks_is_immutable():
    x = da.ones(6, chunks=3)
    try:
        x.chunks = 2
        assert False
    except TypeError as e:
        assert 'rechunk(2)' in str(e)


def test_raise_on_bad_kwargs():
    x = da.ones(5, chunks=3)
    try:
        da.minimum(x, out=None)
    except TypeError as e:
        assert 'minimum' in str(e)
        assert 'out' in str(e)


def test_long_slice():
    x = np.arange(10000)
    d = da.from_array(x, chunks=1)

    assert eq(d[8000:8200], x[8000:8200])


def test_h5py_newaxis():
    try:
        import h5py
    except ImportError:
        return

    with tmpfile('h5') as fn:
        with h5py.File(fn) as f:
            x = f.create_dataset('/x', shape=(10, 10), dtype='f8')
            d = da.from_array(x, chunks=(5, 5))
            assert d[None, :, :].compute(get=get_sync).shape == (1, 10, 10)
            assert d[:, None, :].compute(get=get_sync).shape == (10, 1, 10)
            assert d[:, :, None].compute(get=get_sync).shape == (10, 10, 1)
            assert same_keys(d[:, :, None], d[:, :, None])


def test_ellipsis_slicing():
    assert eq(da.ones(4, chunks=2)[...], np.ones(4))


def test_point_slicing():
    x = np.arange(56).reshape((7, 8))
    d = da.from_array(x, chunks=(3, 4))

    result = d.vindex[[1, 2, 5, 5], [3, 1, 6, 1]]
    assert eq(result, x[[1, 2, 5, 5], [3, 1, 6, 1]])

    result = d.vindex[[0, 1, 6, 0], [0, 1, 0, 7]]
    assert eq(result, x[[0, 1, 6, 0], [0, 1, 0, 7]])
    assert same_keys(result, d.vindex[[0, 1, 6, 0], [0, 1, 0, 7]])


def test_point_slicing_with_full_slice():
    from dask.array.core import _vindex_transpose, _get_axis
    x = np.arange(4*5*6*7).reshape((4, 5, 6, 7))
    d = da.from_array(x, chunks=(2, 3, 3, 4))

    inds = [
            [[1, 2, 3], None, [3, 2, 1], [5, 3, 4]],
            [[1, 2, 3], None, [4, 3, 2], None],
            [[1, 2, 3], [3, 2, 1]],
            [[1, 2, 3], [3, 2, 1], [3, 2, 1], [5, 3, 4]],
            [[], [], [], None],
            [np.array([1, 2, 3]), None, np.array([4, 3, 2]), None],
            [None, None, [1, 2, 3], [4, 3, 2]],
            [None, [0, 2, 3], None, [0, 3, 2]],
            ]

    for ind in inds:
        slc = [i if isinstance(i, (np.ndarray, list)) else slice(None, None)
                for i in ind]
        result = d.vindex[tuple(slc)]

        # Rotate the expected result accordingly
        axis = _get_axis(ind)
        expected = _vindex_transpose(x[tuple(slc)], axis)

        assert eq(result, expected)

        # Always have the first axis be the length of the points
        k = len(next(i for i in ind if isinstance(i, (np.ndarray, list))))
        assert result.shape[0] == k


def test_vindex_errors():
    d = da.ones((5, 5, 5), chunks=(3, 3, 3))
    assert raises(IndexError, lambda: d.vindex[0])
    assert raises(IndexError, lambda: d.vindex[[1, 2, 3]])
    assert raises(IndexError, lambda: d.vindex[[1, 2, 3], [1, 2, 3], 0])
    assert raises(IndexError, lambda: d.vindex[[1], [1, 2, 3]])
    assert raises(IndexError, lambda: d.vindex[[1, 2, 3], [[1], [2], [3]]])

def test_vindex_merge():
    from dask.array.core import _vindex_merge
    locations = [1], [2, 0]
    values = [np.array([[1, 2, 3]]),
              np.array([[10, 20, 30], [40, 50, 60]])]

    assert (_vindex_merge(locations, values) == np.array([[40, 50, 60],
                                                          [1, 2, 3],
                                                          [10, 20, 30]])).all()


def test_empty_array():
    assert eq(np.arange(0), da.arange(0, chunks=5))


def test_array():
    x = np.ones(5, dtype='i4')
    d = da.ones(5, chunks=3, dtype='i4')
    assert eq(da.array(d, ndmin=3, dtype='i8'),
              np.array(x, ndmin=3, dtype='i8'))


def test_cov():
    x = np.arange(56).reshape((7, 8))
    d = da.from_array(x, chunks=(4, 4))

    assert eq(da.cov(d), np.cov(x))
    assert eq(da.cov(d, rowvar=0), np.cov(x, rowvar=0))
    assert eq(da.cov(d, ddof=10), np.cov(x, ddof=10))
    assert eq(da.cov(d, bias=1), np.cov(x, bias=1))
    assert eq(da.cov(d, d), np.cov(x, x))

    y = np.arange(8)
    e = da.from_array(y, chunks=(4,))

    assert eq(da.cov(d, e), np.cov(x, y))
    assert eq(da.cov(e, d), np.cov(y, x))

    assert raises(ValueError, lambda: da.cov(d, ddof=1.5))


def test_memmap():
    with tmpfile('npy') as fn_1:
        with tmpfile('npy') as fn_2:
            try:
                x = da.arange(100, chunks=15)
                target = np.memmap(fn_1, shape=x.shape, mode='w+', dtype=x.dtype)

                x.store(target)

                assert eq(target, x)

                np.save(fn_2, target)

                assert eq(np.load(fn_2, mmap_mode='r'), x)
            finally:
                target._mmap.close()


def test_to_npy_stack():
    x = np.arange(5*10*10).reshape((5, 10, 10))
    d = da.from_array(x, chunks=(2, 4, 4))

    dirname = mkdtemp()
    try:
        da.to_npy_stack(dirname, d, axis=0)
        assert os.path.exists(os.path.join(dirname, '0.npy'))
        assert (np.load(os.path.join(dirname, '1.npy')) == x[2:4]).all()

        e = da.from_npy_stack(dirname)
        assert eq(d, e)
    finally:
        shutil.rmtree(dirname)


def test_view():
    x = np.arange(56).reshape((7, 8))
    d = da.from_array(x, chunks=(2, 3))

    assert eq(x.view('i4'), d.view('i4'))
    assert eq(x.view('i2'), d.view('i2'))
    assert all(isinstance(s, int) for s in d.shape)

    x = np.arange(8, dtype='i1')
    d = da.from_array(x, chunks=(4,))
    assert eq(x.view('i4'), d.view('i4'))

    with pytest.raises(ValueError):
        x = np.arange(8, dtype='i1')
        d = da.from_array(x, chunks=(3,))
        d.view('i4')

    with pytest.raises(ValueError):
        d.view('i4', order='asdf')


def test_view_fortran():
    x = np.asfortranarray(np.arange(64).reshape((8, 8)))
    d = da.from_array(x, chunks=(2, 3))
    assert eq(x.view('i4'), d.view('i4', order='F'))
    assert eq(x.view('i2'), d.view('i2', order='F'))


def test_h5py_tokenize():
    h5py = pytest.importorskip('h5py')
    with tmpfile('hdf5') as fn1:
        with tmpfile('hdf5') as fn2:
            f = h5py.File(fn1)
            g = h5py.File(fn2)

            f['x'] = np.arange(10).astype(float)
            g['x'] = np.ones(10).astype(float)

            x1 = f['x']
            x2 = g['x']

            assert tokenize(x1) != tokenize(x2)


def test_map_blocks_with_changed_dimension():
    x = np.arange(56).reshape((7, 8))
    d = da.from_array(x, chunks=(7, 4))

    e = d.map_blocks(lambda b: b.sum(axis=0), chunks=(4,), drop_axis=0,
                     dtype=d.dtype)
    assert e.ndim == 1
    assert e.chunks == ((4, 4),)
    assert eq(e, x.sum(axis=0))

    x = np.arange(64).reshape((8, 8))
    d = da.from_array(x, chunks=(4, 4))
    e = d.map_blocks(lambda b: b[None, :, :, None],
                     chunks=(1, 4, 4, 1), new_axis=[0, 3], dtype=d.dtype)
    assert e.ndim == 4
    assert e.chunks == ((1,), (4, 4), (4, 4), (1,))
    assert eq(e, x[None, :, :, None])


def test_broadcast_chunks():
    assert broadcast_chunks(((5, 5),), ((5, 5),)) == ((5, 5),)

    a = ((10, 10, 10), (5, 5),)
    b = ((5, 5),)
    assert broadcast_chunks(a, b) == ((10, 10, 10), (5, 5),)
    assert broadcast_chunks(b, a) == ((10, 10, 10), (5, 5),)

    a = ((10, 10, 10), (5, 5),)
    b = ((1,), (5, 5),)
    assert broadcast_chunks(a, b) == ((10, 10, 10), (5, 5),)

    a = ((10, 10, 10), (5, 5),)
    b = ((3, 3,), (5, 5),)
    with pytest.raises(ValueError):
        broadcast_chunks(a, b)


def test_chunks_error():
    x = np.ones((10, 10))
    with pytest.raises(ValueError):
        da.from_array(x, chunks=(5,))


def test_array_compute_forward_kwargs():
    x = da.arange(10, chunks=2).sum()
    x.compute(bogus_keyword=10)
