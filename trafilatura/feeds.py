"""
Examining feeds and extracting links for further processing.
"""

## This file is available from https://github.com/adbar/trafilatura
## under GNU GPL v3 license

import logging
import re

from time import sleep

from .settings import SLEEP_TIME
from .utils import fetch_url

LOGGER = logging.getLogger(__name__)


def validate_url(url):
    '''Superficially check if a given URL could be valid'''
    if re.match(r'https?://[^/]+/.+$', url):
        return True
    return False


def extract_links(feed_string):
    '''Extract links from Atom and RSS feeds'''
    feed_links = list()
    # could be Atom
    if '<link ' in feed_string:
        for item in re.findall(r'<link .*?href="(.+?)"', feed_string):
            feed_links.append(item)
    # could be RSS
    elif '<link>' in feed_string:
        for item in re.findall(r'<link>(.+?)</link>', feed_string):
            feed_links.append(item)
    # sort and uniq
    feed_links = sorted(list(set(feed_links)))
    # control output for validity
    for item in feed_links:
        if validate_url(item) is False:
            feed_links.remove(item)
    # log result
    if len(feed_links) > 0:
        LOGGER.debug('Links found: %s', len(feed_links))
    else:
        LOGGER.debug('Does not seem to be a valid feed')
    return feed_links


def determine_feed(htmlstring):
    '''Try to extract the feed URL from the home page'''
    feed_urls = list()
    # try to find RSS URL
    for feed_url in re.findall(r'type="application/rss\+xml".+?href="(.+?)"', htmlstring):
        feed_urls.append(feed_url)
    # try to find Atom URL
    if len(feed_urls) == 0:
        for feed_url in re.findall(r'type="application/atom\+xml".+?href="(.+?)"', htmlstring):
            feed_urls.append(feed_url)
    for item in feed_urls:
        if 'comments' in item:
            feed_urls.remove(item)
    return feed_urls


def find_feed_urls(url):
    '''Try to find feed URLs'''
    downloaded = fetch_url(url)
    if downloaded is None:
        LOGGER.debug('Could not download web page: %s', url)
        return None
    # assume it's a feed
    if downloaded.startswith('<?xml'):
        feed_links = extract_links(downloaded)
    # assume it's a web page
    else:
        feed_urls = determine_feed(downloaded)
        feed_links = list()
        for feed in feed_urls:
            sleep(SLEEP_TIME)
            feed_string = fetch_url(feed)
            feed_links.extend(extract_links(feed_string))
    return feed_links
