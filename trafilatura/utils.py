# pylint:disable-msg=E0611,I1101
"""
Module bundling functions related to HTML and text processing.
"""
## This file is available from https://github.com/adbar/trafilatura
## under GNU GPL v3 license

# import csv
import gzip
import logging
import re
import sys

from functools import lru_cache


# CChardet is faster and can be more accurate
try:
    import cchardet as chardet
except ImportError:
    import chardet


import urllib3

from lxml import etree, html
# from lxml.html.soupparser import fromstring as fromsoup


LOGGER = logging.getLogger(__name__)


# collect_ids=False, default_doctype=False, huge_tree=True,
HTML_PARSER = html.HTMLParser(remove_comments=True, remove_pis=True, encoding='utf-8')
RECOVERY_PARSER = html.HTMLParser(remove_comments=True, remove_pis=True)

UNICODE_WHITESPACE = re.compile(
    r'''
    \u00A0|\u1680|\u2000|\u2001|\u2002|\u2003|\u2004|\u2005|\u2006|\u2007|
    \u2008|\u2009|\u200a|\u2028|\u2029|\u202F|\u205F|\u3000
    '''
)

NO_TAG_SPACE = re.compile(r'(?<![p{P}>])\n')
SPACE_TRIMMING = re.compile(r'\s+', flags=re.UNICODE|re.MULTILINE)

NOPRINT_TRANS_TABLE = {
    i: None for i in range(0, sys.maxunicode + 1)
    if not chr(i).isprintable() and not chr(i) in (' ', '\t', '\n')
}

# Regex to check image file extensions
IMAGE_EXTENSION = re.compile(r'([^\s]+(\.(jpe?g|png|gif|bmp)))')


def is_gz_file(contents):
    """Tell if a file's magic number corresponds to the GZip format"""
    # source: https://stackoverflow.com/questions/3703276/how-to-tell-if-a-file-is-gzip-compressed
    return contents[:2] == b'\x1f\x8b'


def isutf8(data):
    """Simple heuristic to determine if a bytestring uses standard unicode encoding"""
    try:
        data.decode('UTF-8')
    except UnicodeDecodeError:
        return False
    else:
        return True


def detect_encoding(bytesobject):
    """Read the first chunk of input and return its encoding"""
    #https://github.com/scrapy/w3lib/blob/master/w3lib/encoding.py
    # unicode-test
    if isutf8(bytesobject):
        return 'UTF-8'
    # try one of the installed detectors on first part
    guess = chardet.detect(bytesobject[:1999])
    LOGGER.debug('guessed encoding: %s, confidence: %s', guess['encoding'], guess['confidence'])
    # fallback on full response
    if guess is None or (guess['confidence'] is not None and guess['confidence'] < 0.95):
        guess = chardet.detect(bytesobject)
        LOGGER.debug('second-guessed encoding: %s, confidence: %s', guess['encoding'], guess['confidence'])
    return guess['encoding']


def decode_response(response):
    """Read the urllib3 object corresponding to the server response,
       check if it could be GZip and eventually decompress it, then
       try to guess its encoding and decode it to return a unicode string"""
    # urllib3 response object / bytes switch
    if isinstance(response, bytes):
        resp_content = response
    else:
        resp_content = response.data
    # decode GZipped data
    if is_gz_file(resp_content):
        try:
            resp_content = gzip.decompress(resp_content)
        except (EOFError, OSError):
            logging.warning('invalid GZ file')
    # detect encoding
    guessed_encoding = detect_encoding(resp_content)
    LOGGER.debug('response encoding: %s', guessed_encoding)
    # process
    htmltext = None
    if guessed_encoding is not None:
        try:
            htmltext = resp_content.decode(guessed_encoding)
        except (LookupError, UnicodeDecodeError): # VISCII: lookup
            LOGGER.warning('wrong encoding detected: %s', guessed_encoding)
    else:
        LOGGER.error('no encoding detected: %s', guessed_encoding)
    # force decoding # ascii instead?
    if htmltext is None:
        htmltext = str(resp_content, encoding='utf-8', errors='replace')
        #try:
        #    # frequent error
        #    htmltext = resp_content.decode('cp1252').encode('utf-8')
        #except UnicodeDecodeError:
        #    htmltext = str(resp_content, encoding='utf-8', errors='replace')
    return htmltext


def is_dubious_html(htmlobject):
    "Assess if the object is proper HTML (with a corresponding declaration)."
    if isinstance(htmlobject, bytes):
        if 'html' not in htmlobject[:50].decode(encoding='ascii', errors='ignore').lower():
            return True
    elif isinstance(htmlobject, str):
        if 'html' not in htmlobject[:50].lower():
            return True
    return False


def load_html(htmlobject):
    """Load object given as input and validate its type
    (accepted: LXML tree, bytestring and string)
    """
    # use tree directly
    if isinstance(htmlobject, (etree._ElementTree, html.HtmlElement)):
        return htmlobject
    tree = None
    check_flag = False
    # use urllib3 response directly
    if isinstance(htmlobject, urllib3.response.HTTPResponse):
        htmlobject = decode_response(htmlobject.data)
    # sanity check
    check_flag = is_dubious_html(htmlobject)
    # try to detect encoding and convert to string
    if isinstance(htmlobject, bytes):
        guessed_encoding = detect_encoding(htmlobject)
        if guessed_encoding is not None:
            if guessed_encoding == 'UTF-8':
                tree = html.fromstring(htmlobject, parser=HTML_PARSER)
            else:
                try:
                    htmlobject = htmlobject.decode(guessed_encoding)
                    tree = html.fromstring(htmlobject, parser=HTML_PARSER)
                except (LookupError, UnicodeDecodeError):  # VISCII encoding
                    LOGGER.warning('encoding issue: %s', guessed_encoding)
                    tree = html.fromstring(htmlobject, parser=RECOVERY_PARSER)
        else:
            tree = html.fromstring(htmlobject, parser=RECOVERY_PARSER)
    # use string if applicable
    elif isinstance(htmlobject, str):
        try:
            tree = html.fromstring(htmlobject, parser=HTML_PARSER)
        except ValueError:
            # try to parse a bytestring
            try:
                tree = html.fromstring(htmlobject.encode('utf8'), parser=HTML_PARSER)
            except Exception as err:
                LOGGER.error('parser bytestring %s', err)
        except Exception as err:
            LOGGER.error('parsing failed: %s', err)
    # default to None
    else:
        LOGGER.error('this type cannot be processed: %s', type(htmlobject))
    # rejection test: is it (well-formed) HTML at all?
    if tree is not None and check_flag is True and len(tree) < 2:
        LOGGER.error('parsed tree length: %s, wrong data type or not valid HTML', len(tree))
        tree = None
    #if tree is None:
    #    if isinstance(htmlobject, bytes) or isinstance(htmlobject, str):
    #        # more robust parsing
    #        tree = fromsoup(htmlobject)
    return tree


def txttocsv(text, comments, docmeta):
    '''Output the result in CSV format (tab-separated values)'''
    # outputwriter = csv.writer(sys.stdout, delimiter='\t', quoting=csv.QUOTE_NONE)
    # outputwriter.writerow()
    text = trim(' '.join(text.splitlines()))
    if comments is not None:
        comments = trim(' '.join(comments.splitlines()))
    tsv_output = \
        '{url}\t{fingerprint}\t{hostname}\t{doctitle}\t{docdate}\t{text}\t{comments}\t{textlicense}\n' \
        .format(
        url=docmeta['url'],
        fingerprint=docmeta['fingerprint'],
        hostname=docmeta['hostname'],
        doctitle=docmeta['title'],
        docdate=docmeta['date'],
        text=text,
        comments=comments,
        textlicense=docmeta['license']
        )
    # add id up front if provided
    if docmeta['id'] is not None:
        tsv_output = docmeta['id'] + '\t' + tsv_output
    return tsv_output


@lru_cache(maxsize=128)
def remove_control_characters(string):
    '''Prevent non-printable and XML invalid character errors'''
    # https://stackoverflow.com/questions/92438/stripping-non-printable-characters-from-a-string-in-python/93029#93029
    return string.translate(NOPRINT_TRANS_TABLE)


@lru_cache(maxsize=128)
def line_processing(line):
    '''Discard incompatible unicode and invalid XML characters on line level'''
    # spacing HTML entities: https://www.w3.org/MarkUp/html-spec/html-spec_13.html
    line = line.replace('&#13;', '\r').replace('&#10;', '\n')
    # spaces
    # https://stackoverflow.com/questions/16467479/normalizing-unicode
    # remove non-printable chars
    line = remove_control_characters(UNICODE_WHITESPACE.sub(' ', line))
    line = trim(line)
    if re.match(r'[\s\t]*$', line):
        line = None
    return line


@lru_cache(maxsize=32)
def sanitize(text):
    '''Convert text and discard incompatible and invalid characters'''
    try:
        #returnlines = []
        #for line in text.splitlines():
        #    returnlines.append(line_processing(line))
        # return '\n'.join(list(filter(None.__ne__, returnlines)))
        return '\n'.join([l for l in (line_processing(l) for l in text.splitlines()) if l is not None])
        # return '\n'.join([l for l in map(line_processing, text.splitlines()) if l is not None])
    except AttributeError:
        return None


@lru_cache(maxsize=128)
def trim(string):
    '''Remove unnecessary spaces within a text string'''
    try:
        # remove newlines that are not related to punctuation or markup + proper trimming
        return SPACE_TRIMMING.sub(r' ', NO_TAG_SPACE.sub(r' ', string)).strip(' \t\n\r\v')
    except TypeError:
        return None


def is_image_file(imagesrc):
    '''Check if the observed string corresponds to a valid image extension,
       return False otherwise'''
    if imagesrc is not None and IMAGE_EXTENSION.search(imagesrc):
        return True
    return False


def filter_urls(linklist, urlfilter):
    'Return a list of links corresponding to the given substring pattern.'
    if urlfilter is None:
        return sorted(set(linklist))
    # filter links
    newlist = [l for l in linklist if urlfilter in l]
    # feedburner option
    if len(newlist) == 0:
        newlist = [l for l in linklist if urlfilter in l or 'feedburner' in l or 'feedproxy' in l]
    return sorted(set(newlist))

