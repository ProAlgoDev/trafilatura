# pylint:disable-msg=W1401
"""
Unit tests for download functions from the trafilatura library.
"""

import logging
import os
import sys

try:
    import pycurl
except ImportError:
    pycurl = None

try:
    import brotli
except ImportError:
    brotli = None

import gzip
from time import sleep
from unittest.mock import Mock, patch

from courlan import UrlStore

from trafilatura.cli import parse_args
from trafilatura.cli_utils import (download_queue_processing,
                                   url_processing_pipeline)
from trafilatura.core import extract
from trafilatura.downloads import (DEFAULT_HEADERS, USER_AGENT,
                                   _determine_headers, _handle_response,
                                   _parse_config, _pycurl_is_live_page,
                                   _send_pycurl_request, _send_request,
                                   _urllib3_is_live_page,
                                   add_to_compressed_dict, fetch_url,
                                   is_live_page, load_download_buffer)
from trafilatura.settings import DEFAULT_CONFIG, use_config
from trafilatura.utils import decode_response, load_html

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

ZERO_CONFIG = DEFAULT_CONFIG
ZERO_CONFIG['DEFAULT']['MIN_OUTPUT_SIZE'] = '0'
ZERO_CONFIG['DEFAULT']['MIN_EXTRACTED_SIZE'] = '0'

RESOURCES_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'resources')
UA_CONFIG = use_config(filename=os.path.join(RESOURCES_DIR, 'newsettings.cfg'))


def test_fetch():
    '''Test URL fetching.'''
    # logic: empty request?
    assert _send_request('', True, DEFAULT_CONFIG) is None

    # is_live general tests
    assert _urllib3_is_live_page('https://httpbun.org/status/301') is True
    assert _urllib3_is_live_page('https://httpbun.org/status/404') is False
    assert is_live_page('https://httpbun.org/status/403') is False
    # is_live pycurl tests
    if pycurl is not None:
        assert _pycurl_is_live_page('https://httpbun.org/status/301') is True

    # fetch_url
    assert fetch_url('#@1234') is None
    assert fetch_url('https://httpbun.org/status/404') is None
    # test if the functions default to no_ssl
    # doesn't work?
    # assert _send_request('https://expired.badssl.com/', False, DEFAULT_CONFIG) is not None
    if pycurl is not None:
        assert _send_pycurl_request('https://expired.badssl.com/', False, DEFAULT_CONFIG) is not None
    # no SSL, no decoding
    url = 'https://httpbun.org/status/200'
    response = _send_request('https://httpbun.org/status/200', True, DEFAULT_CONFIG)
    assert response.data == b''
    if pycurl is not None:
        response1 = _send_pycurl_request('https://httpbun.org/status/200', True, DEFAULT_CONFIG)
        assert _handle_response(url, response1, False, DEFAULT_CONFIG) == _handle_response(url, response, False, DEFAULT_CONFIG)
        assert _handle_response(url, response1, True, DEFAULT_CONFIG) == _handle_response(url, response, True, DEFAULT_CONFIG)
    # response object
    # too large response object
    response = Mock()
    response.url = 'https://httpbin.org/encoding/utf8'
    response.status = 200
    # too large
    response.data = b'ABC'*10000000
    assert _handle_response(response.url, response, False, DEFAULT_CONFIG) is None
    # too small
    response.data = b'ABC'
    assert _handle_response(response.url, response, False, DEFAULT_CONFIG) is None
    # straight handling of response object
    with open(os.path.join(RESOURCES_DIR, 'utf8.html'), 'rb') as filehandle:
        response.data = filehandle.read()
    assert _handle_response(response.url, response, False, DEFAULT_CONFIG) is not None
    assert load_html(response) is not None
    # nothing to see here
    assert extract(response, url=response.url, config=ZERO_CONFIG) is None


def test_config():
    '''Test how configuration options are read and stored.'''
    # default config is none
    assert _parse_config(DEFAULT_CONFIG) == (None, None)
    # default accept-encoding
    if brotli is None:
        assert DEFAULT_HEADERS['accept-encoding'].endswith(',deflate')
    else:
        assert DEFAULT_HEADERS['accept-encoding'].endswith(',br')
    # default user-agent
    default = _determine_headers(DEFAULT_CONFIG)
    assert default['User-Agent'] == USER_AGENT
    assert 'Cookie' not in default
    # user-agents rotation
    assert _parse_config(UA_CONFIG) == (['Firefox', 'Chrome'], 'yummy_cookie=choco; tasty_cookie=strawberry')
    custom = _determine_headers(UA_CONFIG)
    assert custom['User-Agent'] in ['Chrome', 'Firefox']
    assert custom['Cookie'] == 'yummy_cookie=choco; tasty_cookie=strawberry'


def test_decode():
    '''Test how responses are being decoded.'''
    # response type
    mock = Mock()
    mock.data = b' '
    assert decode_response(mock) is not None
    # GZip
    html_string = "<html><head/><body><div>ABC</div></body></html>"
    gz_string = gzip.compress(html_string.encode("utf-8"))
    assert decode_response(gz_string) == html_string
    # Brotli
    if brotli is not None:
        brotli_string = brotli.compress(html_string.encode("utf-8"))
        assert decode_response(brotli_string) == html_string


def test_queue():
    'Test creation, modification and download of URL queues.'
    # test conversion and storage
    url_store = add_to_compressed_dict(['ftps://www.example.org/', 'http://'])
    assert isinstance(url_store, UrlStore)
    # download buffer
    inputurls = ['https://test.org/1', 'https://test.org/2', 'https://test.org/3', 'https://test2.org/1', 'https://test2.org/2', 'https://test2.org/3', 'https://test3.org/1', 'https://test3.org/2', 'https://test3.org/3', 'https://test4.org/1', 'https://test4.org/2', 'https://test4.org/3', 'https://test5.org/1', 'https://test5.org/2', 'https://test5.org/3', 'https://test6.org/1', 'https://test6.org/2', 'https://test6.org/3']
    url_store = add_to_compressed_dict(inputurls)
    bufferlist, _ = load_download_buffer(url_store, sleep_time=5)
    assert len(bufferlist) == 6
    sleep(0.25)
    bufferlist, _ = load_download_buffer(url_store, sleep_time=0.1)
    assert len(bufferlist) == 6
    # CLI args
    url_store = add_to_compressed_dict(['https://www.example.org/'])
    testargs = ['', '--list']
    with patch.object(sys, 'argv', testargs):
        args = parse_args(testargs)
    assert url_processing_pipeline(args, url_store) is False
    # single/multiprocessing
    testargs = ['', '-v']
    with patch.object(sys, 'argv', testargs):
        args = parse_args(testargs)
    inputurls = ['https://httpbun.org/status/301', 'https://httpbun.org/status/304', 'https://httpbun.org/status/200', 'https://httpbun.org/status/300', 'https://httpbun.org/status/400', 'https://httpbun.org/status/505']
    url_store = add_to_compressed_dict(inputurls)
    args.archived = True
    args.config_file = os.path.join(RESOURCES_DIR, 'newsettings.cfg')
    config = use_config(filename=args.config_file)
    config['DEFAULT']['SLEEP_TIME'] = '0.2'
    results = download_queue_processing(url_store, args, None, config)
    assert len(results[0]) == 6 and results[1] is None


if __name__ == '__main__':
    test_fetch()
    test_config()
    test_decode()
    test_queue()
