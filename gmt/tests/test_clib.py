# pylint: disable=protected-access
"""
Test the wrappers for the C API.
"""
import os
from contextlib import contextmanager

import pytest
import numpy as np
import numpy.testing as npt
import pandas as pd
import xarray as xr
from packaging.version import Version

from ..clib.core import LibGMT
from ..clib.utils import clib_extension, load_libgmt, check_libgmt, \
    dataarray_to_matrix, get_clib_path
from ..exceptions import GMTCLibError, GMTOSError, GMTCLibNotFoundError, \
    GMTCLibNoSessionError, GMTInvalidInput, GMTVersionError
from ..helpers import GMTTempFile
from .. import Figure


TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


@contextmanager
def mock(lib, func, returns=None, mock_func=None):
    """
    Mock a GMT C API function to make it always return a given value.

    Used to test that exceptions are raised when API functions fail by
    producing a NULL pointer as output or non-zero status codes.

    Needed because it's not easy to get some API functions to fail without
    inducing a Segmentation Fault (which is a good thing because libgmt usually
    only fails with errors).
    """
    if mock_func is None:

        def mock_api_function(*args):  # pylint: disable=unused-argument
            """
            A mock GMT API function that always returns a given value.
            """
            return returns

        mock_func = mock_api_function

    get_libgmt_func = lib.get_libgmt_func

    def mock_get_libgmt_func(name, argtypes=None, restype=None):
        """
        Return our mock function.
        """
        if name == func:
            return mock_func
        return get_libgmt_func(name, argtypes, restype)

    setattr(lib, 'get_libgmt_func', mock_get_libgmt_func)

    yield


def test_load_libgmt():
    "Test that loading libgmt works and doesn't crash."
    load_libgmt()


def test_load_libgmt_fail():
    "Test that loading fails when given a bad library path."
    env = {'GMT_LIBRARY_PATH': 'not/a/real/path'}
    with pytest.raises(GMTCLibNotFoundError):
        load_libgmt(env=env)


def test_get_clib_path():
    "Test that the correct path is found when setting GMT_LIBRARY_PATH."
    # Get the real path to the library first
    with LibGMT() as lib:
        libpath = lib.info['library path']
    libdir = os.path.dirname(libpath)
    # Assign it to the environment variable but keep a backup value to restore
    # later
    env = {'GMT_LIBRARY_PATH': libdir}

    # Check that the path is determined correctly
    path_used = get_clib_path(env=env)
    assert os.path.samefile(path_used, libpath)
    assert os.path.dirname(path_used) == libdir

    # Check that loading libgmt works
    load_libgmt(env=env)


def test_check_libgmt():
    "Make sure check_libgmt fails when given a bogus library"
    with pytest.raises(GMTCLibError):
        check_libgmt(dict())


def test_clib_extension():
    "Make sure we get the correct extension for different OS names"
    for linux in ['linux', 'linux2', 'linux3']:
        assert clib_extension(linux) == 'so'
    assert clib_extension('darwin') == 'dylib'
    with pytest.raises(GMTOSError):
        clib_extension('meh')


def test_constant():
    "Test that I can get correct constants from the C lib"
    lib = LibGMT()
    assert lib.get_constant('GMT_SESSION_EXTERNAL') != -99999
    assert lib.get_constant('GMT_MODULE_CMD') != -99999
    assert lib.get_constant('GMT_PAD_DEFAULT') != -99999
    assert lib.get_constant('GMT_DOUBLE') != -99999
    with pytest.raises(GMTCLibError):
        lib.get_constant('A_WHOLE_LOT_OF_JUNK')


def test_create_destroy_session():
    "Test that create and destroy session are called without errors"
    lib = LibGMT()
    session1 = lib.create_session(session_name='test_session1')
    assert session1 is not None
    session2 = lib.create_session(session_name='test_session2')
    assert session2 is not None
    assert session2 != session1
    lib.destroy_session(session1)
    lib.destroy_session(session2)


def test_create_session_fails():
    "Check that an exception is raised if the session pointer is None"
    lib = LibGMT()
    with mock(lib, 'GMT_Create_Session', returns=None):
        with pytest.raises(GMTCLibError):
            lib.create_session('test-session-name')


def test_destroy_session_fails():
    "Fail to destroy session when given bad input"
    lib = LibGMT()
    with pytest.raises(GMTCLibError):
        lib.destroy_session(None)


def test_set_log_file_fails():
    "log_to_file should fail for invalid file names"
    with LibGMT() as lib:
        with pytest.raises(GMTCLibError):
            with lib.log_to_file(logfile=''):
                print("This should have failed")


def logged_call_module(lib, data_file):
    """
    Make a call_module to 'info' with a log file.
    The call invalid because 'data_file' doesn't exist.
    Checks that the call results in an error and that the correct error message
    is logged.
    """
    msg = 'gmtinfo [ERROR]: Error for input file: No such file ({})'
    mode = lib.get_constant('GMT_MODULE_CMD')
    with lib.log_to_file() as logfile:
        assert os.path.exists(logfile)
        # Make a bogus module call that will fail
        status = lib._libgmt.GMT_Call_Module(lib.current_session,
                                             'info'.encode(), mode,
                                             data_file.encode())
        assert status != 0
        # Check the file content
        with open(logfile) as flog:
            log = flog.read()
        assert log.strip() == msg.format(data_file)
    # Log should be deleted as soon as the with is over
    assert not os.path.exists(logfile)


def test_errors_sent_to_log_file():
    "Make sure error messages are recorded in the log file."
    with LibGMT() as lib:
        logged_call_module(lib, 'not-a-valid-data-file.bla')


def test_set_log_file_twice():
    "Make sure setting a log file twice in a session works"
    with LibGMT() as lib:
        logged_call_module(lib, 'not-a-valid-data-file.bla')
        logged_call_module(lib, 'another-invalid-data-file.bla')


def test_call_module():
    "Run a command to see if call_module works"
    data_fname = os.path.join(TEST_DATA_DIR, 'points.txt')
    out_fname = 'test_call_module.txt'
    with LibGMT() as lib:
        with GMTTempFile() as out_fname:
            lib.call_module('info', '{} -C ->{}'.format(data_fname,
                                                        out_fname.name))
            assert os.path.exists(out_fname.name)
            output = out_fname.read().strip()
            assert output == '11.5309 61.7074 -2.9289 7.8648 0.1412 0.9338'


def test_call_module_error_message():
    "Check that the exception has the error message from call_module"
    data_file = 'bogus-data.bla'
    true_msg = '\n'.join([
        "Command 'info' failed:",
        "---------- Error log ----------",
        'gmtinfo [ERROR]: Error for input file: No such file (bogus-data.bla)',
        "-------------------------------",
    ])
    with LibGMT() as lib:
        # Make a bogus module call that will fail
        try:
            lib.call_module('info', data_file)
        except GMTCLibError as error:
            assert str(error) == true_msg
        else:
            assert False, "Didn't raise an exception"


def test_call_module_invalid_name():
    "Fails when given bad input"
    with LibGMT() as lib:
        with pytest.raises(GMTCLibError):
            lib.call_module('meh', '')


def test_method_no_session():
    "Fails when not in a session"
    # Create an instance of LibGMT without "with" so no session is created.
    lib = LibGMT()
    with pytest.raises(GMTCLibNoSessionError):
        lib.call_module('gmtdefaults', '')
    with pytest.raises(GMTCLibNoSessionError):
        lib.current_session  # pylint: disable=pointless-statement


def test_parse_constant_single():
    "Parsing a single family argument correctly."
    lib = LibGMT()
    for family in lib.data_families:
        parsed = lib._parse_constant(family, valid=lib.data_families)
        assert parsed == lib.get_constant(family)


def test_parse_constant_composite():
    "Parsing a composite constant argument (separated by |) correctly."
    lib = LibGMT()
    test_cases = ((family, via)
                  for family in lib.data_families
                  for via in lib.data_vias)
    for family, via in test_cases:
        composite = '|'.join([family, via])
        expected = lib.get_constant(family) + lib.get_constant(via)
        parsed = lib._parse_constant(composite, valid=lib.data_families,
                                     valid_modifiers=lib.data_vias)
        assert parsed == expected


def test_parse_constant_fails():
    "Check if the function fails when given bad input"
    lib = LibGMT()
    test_cases = [
        'SOME_random_STRING',
        'GMT_IS_DATASET|GMT_VIA_MATRIX|GMT_VIA_VECTOR',
        'GMT_IS_DATASET|NOT_A_PROPER_VIA',
        'NOT_A_PROPER_FAMILY|GMT_VIA_MATRIX',
        'NOT_A_PROPER_FAMILY|ALSO_INVALID',
    ]
    for test_case in test_cases:
        with pytest.raises(GMTInvalidInput):
            lib._parse_constant(test_case, valid=lib.data_families,
                                valid_modifiers=lib.data_vias)

    # Should also fail if not given valid modifiers but is using them anyway.
    # This should work...
    lib._parse_constant('GMT_IS_DATASET|GMT_VIA_MATRIX',
                        valid=lib.data_families, valid_modifiers=lib.data_vias)
    # But this shouldn't.
    with pytest.raises(GMTInvalidInput):
        lib._parse_constant('GMT_IS_DATASET|GMT_VIA_MATRIX',
                            valid=lib.data_families, valid_modifiers=None)


def test_create_data_dataset():
    "Run the function to make sure it doesn't fail badly."
    with LibGMT() as lib:
        # Dataset from vectors
        data_vector = lib.create_data(
            family='GMT_IS_DATASET|GMT_VIA_VECTOR',
            geometry='GMT_IS_POINT',
            mode='GMT_CONTAINER_ONLY',
            dim=[10, 20, 1, 0],  # columns, rows, layers, dtype
        )
        # Dataset from matrices
        data_matrix = lib.create_data(
            family='GMT_IS_DATASET|GMT_VIA_MATRIX',
            geometry='GMT_IS_POINT',
            mode='GMT_CONTAINER_ONLY',
            dim=[10, 20, 1, 0],
        )
        assert data_vector != data_matrix


def test_create_data_grid_dim():
    "Create a grid ignoring range and inc."
    with LibGMT() as lib:
        # Grids from matrices using dim
        lib.create_data(
            family='GMT_IS_GRID|GMT_VIA_MATRIX',
            geometry='GMT_IS_SURFACE',
            mode='GMT_CONTAINER_ONLY',
            dim=[10, 20, 1, 0],
        )


def test_create_data_grid_range():
    "Create a grid specifying range and inc instead of dim."
    with LibGMT() as lib:
        # Grids from matrices using range and int
        lib.create_data(
            family='GMT_IS_GRID|GMT_VIA_MATRIX',
            geometry='GMT_IS_SURFACE',
            mode='GMT_CONTAINER_ONLY',
            ranges=[150., 250., -20., 20.],
            inc=[0.1, 0.2],
        )


def test_create_data_fails():
    "Check that create_data raises exceptions for invalid input and output"
    # Passing in invalid mode
    with pytest.raises(GMTInvalidInput):
        with LibGMT() as lib:
            lib.create_data(
                family='GMT_IS_DATASET',
                geometry='GMT_IS_SURFACE',
                mode='Not_a_valid_mode',
                dim=[0, 0, 1, 0],
                ranges=[150., 250., -20., 20.],
                inc=[0.1, 0.2],
            )
    # Passing in invalid geometry
    with pytest.raises(GMTInvalidInput):
        with LibGMT() as lib:
            lib.create_data(
                family='GMT_IS_GRID',
                geometry='Not_a_valid_geometry',
                mode='GMT_CONTAINER_ONLY',
                dim=[0, 0, 1, 0],
                ranges=[150., 250., -20., 20.],
                inc=[0.1, 0.2],
            )

    # If the data pointer returned is None (NULL pointer)
    with pytest.raises(GMTCLibError):
        with LibGMT() as lib:
            with mock(lib, 'GMT_Create_Data', returns=None):
                lib.create_data(family='GMT_IS_DATASET',
                                geometry='GMT_IS_SURFACE',
                                mode='GMT_CONTAINER_ONLY', dim=[11, 10, 2, 0])


def test_put_vector():
    "Check that assigning a numpy array to a dataset works"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    for dtype in dtypes:
        with LibGMT() as lib:
            dataset = lib.create_data(
                family='GMT_IS_DATASET|GMT_VIA_VECTOR',
                geometry='GMT_IS_POINT',
                mode='GMT_CONTAINER_ONLY',
                dim=[3, 5, 1, 0],  # columns, rows, layers, dtype
            )
            x = np.array([1, 2, 3, 4, 5], dtype=dtype)
            y = np.array([6, 7, 8, 9, 10], dtype=dtype)
            z = np.array([11, 12, 13, 14, 15], dtype=dtype)
            lib.put_vector(dataset, column=lib.get_constant("GMT_X"), vector=x)
            lib.put_vector(dataset, column=lib.get_constant("GMT_Y"), vector=y)
            lib.put_vector(dataset, column=lib.get_constant("GMT_Z"), vector=z)
            # Turns out wesn doesn't matter for Datasets
            wesn = [0]*6
            # Save the data to a file to see if it's being accessed correctly
            with GMTTempFile() as tmp_file:
                lib.write_data('GMT_IS_VECTOR', 'GMT_IS_POINT',
                               'GMT_WRITE_SET', wesn, tmp_file.name, dataset)
                # Load the data and check that it's correct
                newx, newy, newz = tmp_file.loadtxt(unpack=True, dtype=dtype)
                npt.assert_allclose(newx, x)
                npt.assert_allclose(newy, y)
                npt.assert_allclose(newz, z)


def test_put_vector_invalid_dtype():
    "Check that it fails with an exception for invalid data types"
    with LibGMT() as lib:
        dataset = lib.create_data(
            family='GMT_IS_DATASET|GMT_VIA_VECTOR',
            geometry='GMT_IS_POINT',
            mode='GMT_CONTAINER_ONLY',
            dim=[2, 3, 1, 0],  # columns, rows, layers, dtype
        )
        data = np.array([37, 12, 556], dtype='complex128')
        with pytest.raises(GMTInvalidInput):
            lib.put_vector(dataset, column=1, vector=data)


def test_put_vector_wrong_column():
    "Check that it fails with an exception when giving an invalid column"
    with LibGMT() as lib:
        dataset = lib.create_data(
            family='GMT_IS_DATASET|GMT_VIA_VECTOR',
            geometry='GMT_IS_POINT',
            mode='GMT_CONTAINER_ONLY',
            dim=[1, 3, 1, 0],  # columns, rows, layers, dtype
        )
        data = np.array([37, 12, 556], dtype='float32')
        with pytest.raises(GMTCLibError):
            lib.put_vector(dataset, column=1, vector=data)


def test_put_vector_2d_fails():
    "Check that it fails with an exception for multidimensional arrays"
    with LibGMT() as lib:
        dataset = lib.create_data(
            family='GMT_IS_DATASET|GMT_VIA_VECTOR',
            geometry='GMT_IS_POINT',
            mode='GMT_CONTAINER_ONLY',
            dim=[1, 6, 1, 0],  # columns, rows, layers, dtype
        )
        data = np.array([[37, 12, 556], [37, 12, 556]], dtype='int32')
        with pytest.raises(GMTInvalidInput):
            lib.put_vector(dataset, column=0, vector=data)


def test_put_matrix():
    "Check that assigning a numpy 2d array to a dataset works"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    shape = (3, 4)
    for dtype in dtypes:
        with LibGMT() as lib:
            dataset = lib.create_data(
                family='GMT_IS_DATASET|GMT_VIA_MATRIX',
                geometry='GMT_IS_POINT',
                mode='GMT_CONTAINER_ONLY',
                dim=[shape[1], shape[0], 1, 0],  # columns, rows, layers, dtype
            )
            data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
            lib.put_matrix(dataset, matrix=data)
            # wesn doesn't matter for Datasets
            wesn = [0]*6
            # Save the data to a file to see if it's being accessed correctly
            with GMTTempFile() as tmp_file:
                lib.write_data('GMT_IS_MATRIX', 'GMT_IS_POINT',
                               'GMT_WRITE_SET', wesn, tmp_file.name, dataset)
                # Load the data and check that it's correct
                newdata = tmp_file.loadtxt(dtype=dtype)
                npt.assert_allclose(newdata, data)


def test_put_matrix_fails():
    "Check that put_matrix raises an exception if return code is not zero"
    # It's hard to make put_matrix fail on the C API level because of all the
    # checks on input arguments. Mock the C API function just to make sure it
    # works.
    with LibGMT() as lib:
        with mock(lib, 'GMT_Put_Matrix', returns=1):
            with pytest.raises(GMTCLibError):
                lib.put_matrix(dataset=None, matrix=np.empty((10, 2)), pad=0)


def test_put_matrix_grid():
    "Check that assigning a numpy 2d array to a grid works"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    wesn = [10, 15, 30, 40, 0, 0]
    inc = [1, 1]
    shape = ((wesn[3] - wesn[2])//inc[1] + 1, (wesn[1] - wesn[0])//inc[0] + 1)
    for dtype in dtypes:
        with LibGMT() as lib:
            grid = lib.create_data(
                family='GMT_IS_GRID|GMT_VIA_MATRIX',
                geometry='GMT_IS_SURFACE',
                mode='GMT_CONTAINER_ONLY',
                ranges=wesn[:4],
                inc=inc,
                registration='GMT_GRID_NODE_REG',
            )
            data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
            lib.put_matrix(grid, matrix=data)
            # Save the data to a file to see if it's being accessed correctly
            with GMTTempFile() as tmp_file:
                lib.write_data('GMT_IS_MATRIX', 'GMT_IS_SURFACE',
                               'GMT_CONTAINER_AND_DATA',
                               wesn, tmp_file.name, grid)
                # Load the data and check that it's correct
                newdata = tmp_file.loadtxt(dtype=dtype)
                npt.assert_allclose(newdata, data)


def test_virtual_file():
    "Test passing in data via a virtual file with a Dataset"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    shape = (5, 3)
    for dtype in dtypes:
        with LibGMT() as lib:
            family = 'GMT_IS_DATASET|GMT_VIA_MATRIX'
            geometry = 'GMT_IS_POINT'
            dataset = lib.create_data(
                family=family,
                geometry=geometry,
                mode='GMT_CONTAINER_ONLY',
                dim=[shape[1], shape[0], 1, 0],  # columns, rows, layers, dtype
            )
            data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
            lib.put_matrix(dataset, matrix=data)
            # Add the dataset to a virtual file and pass it along to gmt info
            vfargs = (family, geometry, 'GMT_IN', dataset)
            with lib.open_virtual_file(*vfargs) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} ->{}'.format(vfile,
                                                             outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(col.min(), col.max())
                                for col in data.T])
            expected = '<matrix memory>: N = {}\t{}\n'.format(shape[0], bounds)
            assert output == expected


def test_virtual_file_fails():
    """
    Check that opening and closing virtual files raises an exception for
    non-zero return codes
    """
    vfargs = ('GMT_IS_DATASET|GMT_VIA_MATRIX', 'GMT_IS_POINT', 'GMT_IN', None)

    # Mock Open_VirtualFile to test the status check when entering the context.
    # If the exception is raised, the code won't get to the closing of the
    # virtual file.
    with LibGMT() as lib, mock(lib, 'GMT_Open_VirtualFile', returns=1):
        with pytest.raises(GMTCLibError):
            with lib.open_virtual_file(*vfargs):
                print("Should not get to this code")

    # Test the status check when closing the virtual file
    # Mock the opening to return 0 (success) so that we don't open a file that
    # we won't close later.
    with LibGMT() as lib, mock(lib, 'GMT_Open_VirtualFile', returns=0), \
            mock(lib, 'GMT_Close_VirtualFile', returns=1):
        with pytest.raises(GMTCLibError):
            with lib.open_virtual_file(*vfargs):
                pass
            print("Shouldn't get to this code either")


def test_virtual_file_bad_direction():
    "Test passing an invalid direction argument"
    with LibGMT() as lib:
        vfargs = ('GMT_IS_DATASET|GMT_VIA_MATRIX', 'GMT_IS_POINT',
                  'GMT_IS_GRID',  # The invalid direction argument
                  0)
        with pytest.raises(GMTInvalidInput):
            with lib.open_virtual_file(*vfargs):
                print("This should have failed")


def test_vectors_to_vfile():
    "Test the automation for transforming vectors to virtual file dataset"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    size = 10
    for dtype in dtypes:
        x = np.arange(size, dtype=dtype)
        y = np.arange(size, size*2, 1, dtype=dtype)
        z = np.arange(size*2, size*3, 1, dtype=dtype)
        with LibGMT() as lib:
            with lib.vectors_to_vfile(x, y, z) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} ->{}'.format(vfile,
                                                             outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(i.min(), i.max())
                                for i in (x, y, z)])
            expected = '<vector memory>: N = {}\t{}\n'.format(size, bounds)
            assert output == expected


def test_vectors_to_vfile_transpose():
    "Test transforming matrix columns to virtual file dataset"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    shape = (7, 5)
    for dtype in dtypes:
        data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
        with LibGMT() as lib:
            with lib.vectors_to_vfile(*data.T) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} -C ->{}'.format(vfile,
                                                                outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['{:.0f}\t{:.0f}'.format(col.min(), col.max())
                                for col in data.T])
            expected = '{}\n'.format(bounds)
            assert output == expected


def test_vectors_to_vfile_diff_size():
    "Test the function fails for arrays of different sizes"
    x = np.arange(5)
    y = np.arange(6)
    with LibGMT() as lib:
        with pytest.raises(GMTInvalidInput):
            with lib.vectors_to_vfile(x, y):
                print("This should have failed")


def test_matrix_to_vfile():
    "Test transforming a matrix to virtual file dataset"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    shape = (7, 5)
    for dtype in dtypes:
        data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
        with LibGMT() as lib:
            with lib.matrix_to_vfile(data) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} ->{}'.format(vfile,
                                                             outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(col.min(), col.max())
                                for col in data.T])
            expected = '<matrix memory>: N = {}\t{}\n'.format(shape[0], bounds)
            assert output == expected


def test_matrix_to_vfile_slice():
    "Test transforming a slice of a larger array to virtual file dataset"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    shape = (10, 6)
    for dtype in dtypes:
        full_data = np.arange(shape[0]*shape[1], dtype=dtype).reshape(shape)
        rows = 5
        cols = 3
        data = full_data[:rows, :cols]
        with LibGMT() as lib:
            with lib.matrix_to_vfile(data) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} ->{}'.format(vfile,
                                                             outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(col.min(), col.max())
                                for col in data.T])
            expected = '<matrix memory>: N = {}\t{}\n'.format(rows, bounds)
            assert output == expected


def test_vectors_to_vfile_pandas():
    "Pass vectors to a dataset using pandas Series"
    dtypes = 'float32 float64 int32 int64 uint32 uint64'.split()
    size = 13
    for dtype in dtypes:
        data = pd.DataFrame(
            data=dict(x=np.arange(size, dtype=dtype),
                      y=np.arange(size, size*2, 1, dtype=dtype),
                      z=np.arange(size*2, size*3, 1, dtype=dtype))
        )
        with LibGMT() as lib:
            with lib.vectors_to_vfile(data.x, data.y, data.z) as vfile:
                with GMTTempFile() as outfile:
                    lib.call_module('info', '{} ->{}'.format(vfile,
                                                             outfile.name))
                    output = outfile.read(keep_tabs=True)
            bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(i.min(), i.max())
                                for i in (data.x, data.y, data.z)])
            expected = '<vector memory>: N = {}\t{}\n'.format(size, bounds)
            assert output == expected


def test_vectors_to_vfile_arraylike():
    "Pass array-like vectors to a dataset"
    size = 13
    x = list(range(0, size, 1))
    y = tuple(range(size, size*2, 1))
    z = range(size*2, size*3, 1)
    with LibGMT() as lib:
        with lib.vectors_to_vfile(x, y, z) as vfile:
            with GMTTempFile() as outfile:
                lib.call_module('info', '{} ->{}'.format(vfile, outfile.name))
                output = outfile.read(keep_tabs=True)
        bounds = '\t'.join(['<{:.0f}/{:.0f}>'.format(min(i), max(i))
                            for i in (x, y, z)])
        expected = '<vector memory>: N = {}\t{}\n'.format(size, bounds)
        assert output == expected


def test_extract_region_fails():
    "Check that extract region fails if nothing has been plotted."
    Figure()
    with pytest.raises(GMTCLibError):
        with LibGMT() as lib:
            lib.extract_region()


def test_extract_region_two_figures():
    "Extract region should handle multiple figures existing at the same time"
    # Make two figures before calling extract_region to make sure that it's
    # getting from the current figure, not the last figure.
    fig1 = Figure()
    region1 = np.array([0, 10, -20, -10])
    fig1.coast(region=region1, projection="M6i", frame=True, land='black')

    fig2 = Figure()
    fig2.basemap(region='US.HI+r5', projection="M6i", frame=True)

    # Activate the first figure and extract the region from it
    # Use in a different session to avoid any memory problems.
    with LibGMT() as lib:
        lib.call_module('figure', '{} -'.format(fig1._name))
    with LibGMT() as lib:
        wesn1 = lib.extract_region()
        npt.assert_allclose(wesn1, region1)

    # Now try it with the second one
    with LibGMT() as lib:
        lib.call_module('figure', '{} -'.format(fig2._name))
    with LibGMT() as lib:
        wesn2 = lib.extract_region()
        npt.assert_allclose(wesn2, np.array([-165., -150., 15., 25.]))


def test_write_data_fails():
    "Check that write data raises an exception for non-zero return codes"
    # It's hard to make the C API function fail without causing a Segmentation
    # Fault. Can't test this if by giving a bad file name because if
    # output=='', GMT will just write to stdout and spaces are valid file
    # names. Use a mock instead just to exercise this part of the code.
    with LibGMT() as lib:
        with mock(lib, 'GMT_Write_Data', returns=1):
            with pytest.raises(GMTCLibError):
                lib.write_data('GMT_IS_VECTOR', 'GMT_IS_POINT',
                               'GMT_WRITE_SET', [1]*6, 'some-file-name', None)


def test_dataarray_to_matrix_dims_fails():
    "Check that it fails for > 2 dims"
    # Make a 3D regular grid
    data = np.ones((10, 12, 11), dtype='float32')
    x = np.arange(11)
    y = np.arange(12)
    z = np.arange(10)
    grid = xr.DataArray(data, coords=[('z', z), ('y', y), ('x', x)])
    with pytest.raises(GMTInvalidInput):
        dataarray_to_matrix(grid)


def test_dataarray_to_matrix_inc_fails():
    "Check that it fails for variable increments"
    data = np.ones((4, 5), dtype='float64')
    x = np.linspace(0, 1, 5)
    y = np.logspace(2, 3, 4)
    grid = xr.DataArray(data, coords=[('y', y), ('x', x)])
    with pytest.raises(GMTInvalidInput):
        dataarray_to_matrix(grid)


def test_get_default():
    "Make sure get_default works without crashing and gives reasonable results"
    with LibGMT() as lib:
        assert lib.get_default('API_GRID_LAYOUT') in ['rows', 'columns']
        assert int(lib.get_default('API_CORES')) >= 1
        assert Version(lib.get_default('API_VERSION')) >= Version('6.0.0')


def test_get_default_fails():
    "Make sure get_default raises an exception for invalid names"
    with LibGMT() as lib:
        with pytest.raises(GMTCLibError):
            lib.get_default('NOT_A_VALID_NAME')


def test_info_dict():
    "Make sure the LibGMT.info dict is working."
    # Check if there are no errors or segfaults from getting all of the
    # properties.
    with LibGMT() as lib:
        assert lib.info

    # Mock GMT_Get_Default to return always the same string
    def mock_defaults(api, name, value):  # pylint: disable=unused-argument
        "Put 'bla' in the value buffer"
        value.value = b"bla"
        return 0

    with LibGMT() as lib:
        with mock(lib, 'GMT_Get_Default', mock_func=mock_defaults):
            info = lib.info
            # Check for an empty dictionary
            assert info
            for key in info:
                assert info[key] == 'bla'


def test_fails_for_wrong_version():
    "Make sure the LibGMT raises an exception if GMT is too old"

    # Mock GMT_Get_Default to return an old version
    def mock_defaults(api, name, value):  # pylint: disable=unused-argument
        "Return an old version"
        if name == b'API_VERSION':
            value.value = b"5.4.3"
        else:
            value.value = b"bla"
        return 0

    lib = LibGMT()
    with mock(lib, 'GMT_Get_Default', mock_func=mock_defaults):
        with pytest.raises(GMTVersionError):
            with lib:
                assert lib.info['version'] != '5.4.3'
    # Make sure the session is closed when the exception is raised.
    with pytest.raises(GMTCLibNoSessionError):
        assert lib.current_session
