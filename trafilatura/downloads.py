# pylint:disable-msg=E0611,I1101
"""
All functions needed to steer and execute downloads of web documents.
"""


import logging
import random
import re

from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep

import certifi

try:
    import pycurl
    CURL_SHARE = pycurl.CurlShare()
    # available options:
    # https://curl.se/libcurl/c/curl_share_setopt.html
    CURL_SHARE.setopt(pycurl.SH_SHARE, pycurl.LOCK_DATA_DNS)
    CURL_SHARE.setopt(pycurl.SH_SHARE, pycurl.LOCK_DATA_SSL_SESSION)
    # not thread-safe
    # CURL_SHARE.setopt(pycurl.SH_SHARE, pycurl.LOCK_DATA_CONNECT)
except ImportError:
    pycurl = None

import urllib3

from courlan import UrlStore

from . import __version__
from .settings import DEFAULT_CONFIG, DOWNLOAD_THREADS
from .utils import decode_response, uniquify_list


URL_STORE = UrlStore(compressed=False, strict=False)

NUM_CONNECTIONS = 50
MAX_REDIRECTS = 2

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HTTP_POOL = None
NO_CERT_POOL = None
RETRY_STRATEGY = None

DEFAULT_HEADERS = urllib3.util.make_headers(accept_encoding=True)
USER_AGENT = 'trafilatura/' + __version__ + ' (+https://github.com/adbar/trafilatura)'
DEFAULT_HEADERS['User-Agent'] = USER_AGENT

LOGGER = logging.getLogger(__name__)

RawResponse = namedtuple('RawResponse', ['data', 'status', 'url'])


# caching throws an error
# @lru_cache(maxsize=2)
def _parse_config(config):
    'Read and extract HTTP header strings from the configuration file.'
    # load a series of user-agents
    myagents = config.get('DEFAULT', 'USER_AGENTS').strip() or None
    if myagents is not None and myagents != '':
        myagents = myagents.split("\n")
    # https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies
    # todo: support for several cookies?
    mycookie = config.get('DEFAULT', 'COOKIE') or None
    return myagents, mycookie


def _determine_headers(config, headers=None):
    'Internal function to decide on user-agent string.'
    if config != DEFAULT_CONFIG:
        myagents, mycookie = _parse_config(config)
        headers = {}
        if myagents is not None:
            rnumber = random.randint(0, len(myagents) - 1)
            headers['User-Agent'] = myagents[rnumber]
        if mycookie is not None:
            headers['Cookie'] = mycookie
    return headers or DEFAULT_HEADERS


def _send_request(url, no_ssl, config):
    "Internal function to robustly send a request (SSL or not) and return its result."
    # customize headers
    global HTTP_POOL, NO_CERT_POOL, RETRY_STRATEGY
    if not RETRY_STRATEGY:
        RETRY_STRATEGY = urllib3.util.Retry(
            total=0,
            redirect=MAX_REDIRECTS, # raise_on_redirect=False,
            connect=0,
            backoff_factor=config.getint('DEFAULT', 'DOWNLOAD_TIMEOUT')*2,
            status_forcelist=[
                429, 499, 500, 502, 503, 504, 509, 520, 521, 522, 523, 524, 525, 526, 527, 530, 598
            ],
            # unofficial: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes#Unofficial_codes
        )
    try:
        # TODO: read by streaming chunks (stream=True, iter_content=xx)
        # so we can stop downloading as soon as MAX_FILE_SIZE is reached
        if no_ssl is False:
            # define pool
            if not HTTP_POOL:
                HTTP_POOL = urllib3.PoolManager(retries=RETRY_STRATEGY, timeout=config.getint('DEFAULT', 'DOWNLOAD_TIMEOUT'), ca_certs=certifi.where(), num_pools=NUM_CONNECTIONS)  # cert_reqs='CERT_REQUIRED'
            # execute request
            response = HTTP_POOL.request('GET', url, headers=_determine_headers(config))
        else:
            # define pool
            if not NO_CERT_POOL:
                NO_CERT_POOL = urllib3.PoolManager(retries=RETRY_STRATEGY, timeout=config.getint('DEFAULT', 'DOWNLOAD_TIMEOUT'), cert_reqs='CERT_NONE', num_pools=NUM_CONNECTIONS)
            # execute request
            response = NO_CERT_POOL.request('GET', url, headers=_determine_headers(config))
    except urllib3.exceptions.SSLError:
        LOGGER.error('retrying after SSLError: %s', url)
        return _send_request(url, True, config)
    except Exception as err:
        logging.error('download error: %s %s', url, err)  # sys.exc_info()[0]
    else:
        # necessary for standardization
        return RawResponse(response.data, response.status, response.geturl())
    # catchall
    return None


def _handle_response(url, response, decode, config):
    'Internal function to run safety checks on response result.'
    if response.status != 200:
        LOGGER.error('not a 200 response: %s for URL %s', response.status, url)
    elif response.data is None or len(response.data) < config.getint('DEFAULT', 'MIN_FILE_SIZE'):
        LOGGER.error('too small/incorrect for URL %s', url)
        # raise error instead?
    elif len(response.data) > config.getint('DEFAULT', 'MAX_FILE_SIZE'):
        LOGGER.error('too large: length %s for URL %s', len(response.data), url)
        # raise error instead?
    else:
        return decode_response(response.data) if decode is True else response
    # catchall
    return None


def fetch_url(url, decode=True, no_ssl=False, config=DEFAULT_CONFIG):
    """Fetches page using urllib3 and decodes the response.

    Args:
        url: URL of the page to fetch.
        decode: Decode response instead of returning urllib3 response object (boolean).
        no_ssl: Don't try to establish a secure connection (to prevent SSLError).
        config: Pass configuration values for output control.

    Returns:
        RawResponse object: data (headers + body), status (HTML code as string) and url
        or None in case the result is invalid or there was a problem with the network.

    """
    LOGGER.debug('sending request: %s', url)
    if pycurl is None:
        response = _send_request(url, no_ssl, config)
    else:
        response = _send_pycurl_request(url, no_ssl, config)
    if response is not None and response != '':
        return _handle_response(url, response, decode, config)
        # return '' (useful do discard further processing?)
        # return response
    LOGGER.debug('no response: %s', url)
    return None


def add_to_compressed_dict(inputlist, blacklist=None, url_filter=None, url_store=None):
    '''Filter, convert input URLs and add them to domain-aware processing dictionary'''
    # init
    if url_store is None:
        url_store = UrlStore(compressed=False, strict=False)
    # deduplicate while keeping order
    inputlist = uniquify_list(inputlist)
    # filter
    if blacklist:
        inputlist = [u for u in inputlist if re.sub(r'https?://', '', u) not in blacklist]
    if url_filter:
        filtered_list = []
        while inputlist:
            u = inputlist.pop()
            for f in url_filter:
                if f in u:
                    filtered_list.append(u)
                    break
        inputlist = filtered_list
    # validate and store
    url_store.add_urls(inputlist)
    return url_store


def load_download_buffer(url_store, sleep_time=5, threads=DOWNLOAD_THREADS):
    '''Determine threading strategy and draw URLs respecting domain-based back-off rules.'''
    # the remaining list is too small, process it differently
    if len([d for d in url_store.urldict if url_store.urldict[d].all_visited is False]) < threads:
        threads = 1
    bufferlist = []
    while not bufferlist:
        bufferlist = url_store.get_download_urls(timelimit=sleep_time)
        # add emptiness test or sleep?
        if not bufferlist:
            if url_store.done is False:
                sleep(sleep_time)
            else:
                bufferlist = []
                break
    return bufferlist, threads, url_store


def buffered_downloads(bufferlist, download_threads, decode=True):
    '''Download queue consumer, single- or multi-threaded.'''
    # start several threads
    with ThreadPoolExecutor(max_workers=download_threads) as executor:
        future_to_url = {executor.submit(fetch_url, url, decode): url for url in bufferlist}
        for future in as_completed(future_to_url):
            # url and download result
            yield future_to_url[future], future.result()


def _send_pycurl_request(url, no_ssl, config):
    '''Experimental function using libcurl and pycurl to speed up downloads'''
    # https://github.com/pycurl/pycurl/blob/master/examples/retriever-multi.py

    # init
    # headerbytes = BytesIO()
    headers = _determine_headers(config)
    headerlist = ['Accept-Encoding: gzip, deflate', 'Accept: */*']
    for header, content in headers.items():
        headerlist.append(header + ': ' + content)

    # prepare curl request
    # https://curl.haxx.se/libcurl/c/curl_easy_setopt.html
    curl = pycurl.Curl()
    curl.setopt(pycurl.URL, url.encode('utf-8'))
    # share data
    curl.setopt(pycurl.SHARE, CURL_SHARE)
    curl.setopt(pycurl.HTTPHEADER, headerlist)
    # curl.setopt(pycurl.USERAGENT, '')
    curl.setopt(pycurl.FOLLOWLOCATION, 1)
    curl.setopt(pycurl.MAXREDIRS, MAX_REDIRECTS)
    curl.setopt(pycurl.CONNECTTIMEOUT, config.getint('DEFAULT', 'DOWNLOAD_TIMEOUT'))
    curl.setopt(pycurl.TIMEOUT, config.getint('DEFAULT', 'DOWNLOAD_TIMEOUT'))
    curl.setopt(pycurl.NOSIGNAL, 1)
    if no_ssl is True:
        curl.setopt(pycurl.SSL_VERIFYPEER, 0)
        curl.setopt(pycurl.SSL_VERIFYHOST, 0)
    else:
        curl.setopt(pycurl.CAINFO, certifi.where())
    curl.setopt(pycurl.MAXFILESIZE, config.getint('DEFAULT', 'MAX_FILE_SIZE'))
    #curl.setopt(pycurl.HEADERFUNCTION, headerbytes.write)
    #curl.setopt(pycurl.WRITEDATA, bufferbytes)
    # TCP_FASTOPEN
    # curl.setopt(pycurl.FAILONERROR, 1)
    # curl.setopt(pycurl.ACCEPT_ENCODING, '')

    # send request
    try:
        bufferbytes = curl.perform_rb()
    except pycurl.error as err:
        logging.error('pycurl error: %s %s', url, err)
        # retry in case of SSL-related error
        # see https://curl.se/libcurl/c/libcurl-errors.html
        # errmsg = curl.errstr_raw()
        # additional error codes: 80, 90, 96, 98
        if no_ssl is False and err.args[0] in (35, 54, 58, 59, 60, 64, 66, 77, 82, 83, 91):
            LOGGER.debug('retrying after SSL error: %s %s', url, err)
            return _send_pycurl_request(url, True, config)
        # traceback.print_exc(file=sys.stderr)
        # sys.stderr.flush()
        return None

    # https://github.com/pycurl/pycurl/blob/master/examples/quickstart/response_headers.py
    #respheaders = dict()
    #for header_line in headerbytes.getvalue().decode('iso-8859-1').splitlines(): # re.split(r'\r?\n',
    #    # This will botch headers that are split on multiple lines...
    #    if ':' not in header_line:
    #        continue
    #    # Break the header line into header name and value.
    #    name, value = header_line.split(':', 1)
    #    # Now we can actually record the header name and value.
    #    respheaders[name.strip()] = value.strip() # name.strip().lower() ## TODO: check
    # status
    respcode = curl.getinfo(curl.RESPONSE_CODE)
    # url
    effective_url = curl.getinfo(curl.EFFECTIVE_URL)
    # additional info
    # ip_info = curl.getinfo(curl.PRIMARY_IP)

    # tidy up
    curl.close()
    return RawResponse(bufferbytes, respcode, effective_url)
