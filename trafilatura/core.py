# -*- coding: utf-8 -*-
# pylint:disable-msg=E0611,I1101
"""
Module bundling all functions needed to extract the text in a webpage.
"""

## This file is available from https://github.com/adbar/trafilatura
## under GNU GPL v3 license

## TODO:
# line-based heuristics?
# text blacklist

# standard
import logging
import re # import regex as re
from collections import defaultdict

# third-party
import justext
# from justext import classify_paragraphs, get_stoplist, revise_paragraph_classification
try:
    import langid
    LANGID_FLAG = True
except ImportError:
    LANGID_FLAG = False
try:
    from htmldate import find_date
    DATE_FLAG = True
except ImportError:
    DATE_FLAG = False
try:
    from lru import LRU # https://github.com/amitdev/lru-dict # pip3 install lru-dict
    LRU_FLAG = True
except ImportError:
    LRU_FLAG = False
from lxml import etree, html

# own
from .settings import CUT_EMPTY_ELEMS, HTML_CLEANER, LANGUAGES, LRU_SIZE, \
MANUALLY_CLEANED, MIN_DUPLCHECK_SIZE, MIN_EXTRACTED_SIZE, MIN_EXTRACTED_COMM_SIZE, TAG_CATALOG
from .utils import load_html, sanitize, trim
from .xml import check_tei, validate_tei, write_teitree, xmltotxt
from .xpaths import BODY_XPATH, COMMENTS_XPATH, COMMENTS_DISCARD_XPATH, DISCARD_XPATH

## INIT

if LANGID_FLAG is True:
    langid.set_languages(LANGUAGES)

LOGGER = logging.getLogger(__name__)

COMMENTS_BLACKLIST = ('( Abmelden / Ändern )')

# counters
if LRU_FLAG is True:
    LRU_TEST = LRU(LRU_SIZE)
else:
    LRU_TEST = defaultdict(int)
# tree_cache = dict()

# justext
JUSTEXT_STOPLIST = justext.get_stoplist('German')


#@profile
def manual_cleaning(tree, include_tables):
    '''Prune the tree by discard unwanted elements'''
    #for element in tree.xpath('//*'):
    #    print('ZZZ ', element.tag)
    if include_tables is False:
        MANUALLY_CLEANED.append('table')
    for expression in MANUALLY_CLEANED:
        for element in tree.iter(expression):
            element.getparent().remove(element)
    #for expression in ['a', 'abbr', 'acronym', 'address', 'big', 'cite', 'font', 'ins', 'meta', 'small', 'sub', 'sup', 'wbr']:
    #    for element in tree.getiterator(expression):
    #        element.drop_tag()
    return tree


#@profile
def prune_html(tree):
    '''delete empty elements'''
    # empty tags
    for element in tree.xpath(".//*[not(node())]"):
        if element.tag in CUT_EMPTY_ELEMS:
            element.getparent().remove(element)
        #else:
        #    print('ZZZ ', element.tag)
    #for expression in CUT_EMPTY_ELEMS:
    #    for element in tree.getiterator(expression):
    #        if recursively_empty(element):
    #            element.getparent().remove(element)
    return tree


#@profile
def recursively_empty(elem):
    '''return recursively empty elements'''
    # https://stackoverflow.com/questions/12694091/python-lxml-how-to-remove-empty-repeated-tags
    if elem.text:
        return False
    return all((recursively_empty(c) for c in elem.iterchildren()))


#@profile
def discard_unwanted(tree):
    '''delete unwanted sections'''
    for expr in DISCARD_XPATH:
        for subtree in tree.xpath(expr):
            subtree.getparent().remove(subtree)
    return tree


#@profile
def discard_unwanted_comments(tree):
    '''delete unwanted comment sections'''
    for expr in COMMENTS_DISCARD_XPATH:
        for subtree in tree.xpath(expr):
            subtree.getparent().remove(subtree)
    return tree


#@profile
def textfilter(element):
    '''Filter out unwanted text'''
    # print('#', element.text)
    if element.text is None and element.tail is not None:
        testtext = element.tail
    else:
        testtext = element.text
    for line in testtext.splitlines():
        #if len(line) <= 5:
        #    continue
        # print('###', line) |.hnliche Beitr| Instagram
        if re.match(r'\W*(Gef.llt mir|[Ss]hare (on|via)|Fill in your details below|Trage deine Daten unten|Kommentar verfassen|Bitte logge dich|Hinterlasse einen Kommentar| to %s| mit %s)', line) or re.match(r'\W*(Facebook|Twitter|Google|E-Mail|Drucken|Linked[Ii]n|Whats[Aa]pp|XING|[Xx]ing|PDF|[Pp]df)$', line):
            return True
        if re.search(r'Tags: [A-ZÄÖÜßa-zäöü ,]+', line):
            return True
    # elemtext = trim(elemtext)
    #return elemtext
    return False


def cache(body):
    '''Implement LRU cache'''
    global LRU_TEST
    for element in body:
        # teststring = ' '.join(element.itertext()).encode('utf-8')
        teststring = element.text
        if LRU_FLAG is True:
            if LRU_TEST.has_key(teststring) is True:
                LRU_TEST[teststring] += 1
            else:
                LRU_TEST[teststring] = 1
        else:
            LRU_TEST[teststring] += 1


def duplicate_test(element, justext_switch=False):
    '''Check for duplicate text'''
    global LRU_TEST
    # teststring = ' '.join(element.itertext()).encode('utf-8')
    if justext_switch is False:
        teststring = element.text_content()
    else:
        teststring = element.text
    if len(teststring) > MIN_DUPLCHECK_SIZE:
        if LRU_FLAG is True:
            if LRU_TEST.has_key(teststring) is True and LRU_TEST[teststring] > 2:
                # LRU_TEST[teststring] += 1
                return True
        else:
            if teststring in LRU_TEST and LRU_TEST[teststring] > 2:
                return True
            # LRU_TEST[teststring] += 1
    return False


#@profile
def convert_tags(tree):
    '''Simplify markup and convert relevant HTML tags to an XML standard'''
    # strip tags
    etree.strip_tags(tree, 'a', 'abbr', 'acronym', 'address', 'big', 'cite', 'dd', 'font', 'ins', 'meta', 'span', 'small', 'sub', 'sup', 'wbr')
    # head tags + delete attributes
    for elem in tree.iter('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        # print(elem.tag, elem.text_content())
        # etree.strip_tags(elem, 'span')
        elem.tag = 'head'
        # elem.set('rendition', '#i')
    # br → lb
    for elem in tree.iter('br', 'hr'): # tree.xpath('//[br or hr]'): ## hr → //lb/line ?
        elem.tag = 'lb'
        elem.attrib.clear()
    # ul/ol → list / li → item
    for elem in tree.iter('ul', 'ol', 'dl'):
        elem.tag = 'list'
        elem.attrib.clear()
    # blockquote | q → quote
    for elem in tree.iter('blockquote', 'pre', 'q'):
        elem.tag = 'quote'
        elem.attrib.clear()
    # change rendition #i
    for elem in tree.iter('em', 'i'):
        elem.attrib.clear()
        elem.tag = 'hi'
        elem.set('rendition', '#i')
    # change rendition #b
    for elem in tree.iter('b', 'strong'):
        elem.attrib.clear()
        elem.tag = 'hi'
        elem.set('rendition', '#b')
    # change rendition #u (very rare)
    for elem in tree.iter('u'):
        elem.tag = 'hi'
        elem.set('rendition', '#u')
    # change rendition #pre and #t (very rare)
    for elem in tree.iter('tt'): # //pre| //code
        elem.attrib.clear()
        elem.tag = 'hi'
        elem.set('rendition', '#t')
    # change rendition sub and sup (very rare)
    for elem in tree.iter('sub'): # //pre| //code
        elem.attrib.clear()
        elem.tag = 'hi'
        elem.set('rendition', '#sub')
    for elem in tree.iter('sup'): # //pre| //code
        elem.attrib.clear()
        elem.tag = 'hi'
        elem.set('rendition', '#sup')
    # del | s | strike → <del rend="overstrike">
    for elem in tree.iter('del', 's', 'strike'):
        elem.attrib.clear()
        elem.tag = 'del'
        elem.set('rendition', 'overstrike')
    # add space
    #for elem in tree.iter('span'): # //a|
    #    elem.drop_tag()
        #if elem.text is None:
        #    elem.text = ' '
        #else:
        #    elem.text = elem.text + ' '
    return tree


def try_justext(tree, url):
    '''Safety net: try with the generic algorithm justext'''
    result_body = etree.Element('body')
    justtextstring = html.tostring(tree, pretty_print=False, encoding='unicode')
    LOGGER.debug('raw length: %s (tostring) ', len(justtextstring))
    try:
        # paragraphs = custom_justext(tree)
        paragraphs = justext.justext(justtextstring, JUSTEXT_STOPLIST)
    except ValueError as err: # ValueError: Input object is not an XML element: HtmlComment
        LOGGER.error('justext %s %s', err, url)
        result_body = None
    else:
        for paragraph in paragraphs:
            if not paragraph.is_boilerplate:
                if duplicate_test(paragraph, justext_switch=True) is not True:
                    elem = etree.Element('p')
                    elem.text = paragraph.text
                    result_body.append(elem)
    return result_body


#@profile
def handle_textnode(element, comments_fix=True):
    '''Convert, format, and probe potential text elements'''
    # lb bypass
    if comments_fix is False and element.tag == 'lb':
        return element
    if element.tag in ('head', 'dl', 'ol', 'ul'):
        element.attrib.clear()
    if element.text is None: # or len(element.text) < 10 # text_content()
        # try the tail
        if element.tail is None or len(element.tail) < 2: # was 50
            #element.getparent().remove(element)
            #continue
            return None
        # LOGGER.debug('using tail for element %s', element.tag)
        element.text = element.tail
        element.tail = ''
        # handle differently for br/lb
        if comments_fix is True and element.tag == 'lb':
            element.tag = 'p'
    # delete newlines that are not related to punctuation or markup
    # element.text = re.sub(r'(?<![p{P}>])\n', ' ', element.text)
    # trim
    element.text = trim(element.text) # + '\n'
    # if element.tail:
    element.tail = trim(element.tail) # + '\n'
    ## LOGGER.debug(element.tag, element.text)
    if element.text and re.search(r'\w', element.text): # text_content()
        if textfilter(element) is True:
            return None
        ## TODO: improve duplicate detection
        if duplicate_test(element) is True:
            return None
    else:
        return None
    return element


#@profile
#def handle_subelement(subelement):
#    '''Convert, format, and probe potential text subelements'''
#    if subelement.text is None and subelement.tail is None:
#        return subelement
    # delete newlines that are not related to punctuation or markup
#    subelement.tail = re.sub(r'(?<![p{P}>])\n', ' ', subelement.tail)
#    # trim
#    subelement.tail = trim(subelement.tail) + '\n'
#    if textfilter(subelement) is True:
#        return None
    #if duplicate_test(subelement) is True:
    #    return None
#    return subelement


#@profile
def extract_content(tree, include_tables=False):
    '''Find and extract the main content of a page using a set of expressions'''
    #tree_cache = dict()
    #tree_cache[tree] = list(tree.iter())
    result_body = etree.Element('body')
    # iterate
    for expr in BODY_XPATH:
        # select tree if the expression has been found
        subtree = tree.xpath(expr)
        if len(subtree) == 0:
            continue
        #print(expr, len(subtree))
        subtree = subtree[0]
        # prune
        subtree = discard_unwanted(subtree)
        # etree.strip_tags(subtree, 'lb') # BoingBoing-Bug
        #print(html.tostring(subtree, pretty_print=True, encoding='unicode'))
        # define iteration strategy
        potential_tags = set(TAG_CATALOG) # 'span'
        # relevant_tags = potential_tags - set('lb')
        if len(subtree.xpath('//p//text()')) == 0: # no paragraphs containing text
            potential_tags.add('div')
        LOGGER.debug(sorted(potential_tags))
        # extract content
        for element in subtree.xpath('.//*'): # .iter() .getchildren() .xpath('.//*')
            # print(element.tag, element.text)
            # bypass: nested elements
            if element.tag in ('list', 'quote'):
                processed_element = etree.Element(element.tag)
                for child in element.iter():
                    # list-specific check
                    if element.tag == 'list' and child.tag not in ('dt', 'li'): # 'item'
                        continue
                    # proceed with iteration, fix for nested elements
                    processed_child = handle_textnode(child, comments_fix=True)
                    if processed_child is not None:
                        if element.tag == 'list':
                            newsub = etree.SubElement(processed_element, 'item')
                        else:
                            newsub = etree.SubElement(processed_element, child.tag)
                        newsub.text = processed_child.text
                        if element.tag == 'quote':
                            newsub.tail = processed_child.tail
                    # child.getparent().remove(child)
                    child.tag = 'done' # can cause errors
                # avoid double tags??
                if len(processed_element) > 0: # if it has children
                    #teststring = ''.join(processed_element.itertext())
                    #if len(teststring) > 0 and re.search(r'[a-z]', teststring): # if it has text
                    # correct nested elements
                    if processed_element.tag == 'quote':
                        etree.strip_tags(processed_element, 'quote')
                        # processed_element.tag == 'quote' #superfluous?
                    result_body.append(processed_element)
            # bypass: head:
            elif element.tag == 'head':
                element.text = trim(element.text)
                if element.text and re.search(r'\w', element.text):
                    element.attrib.clear()
                    result_body.append(element)
            # strip attrs after discard is run
            elif element.tag == 'p':
                element.attrib.clear()
                # no children
                if len(element) == 0:
                    processed_element = handle_textnode(element, comments_fix=False)
                    if processed_element is not None:
                        result_body.append(processed_element)
                    continue
                # children
                processed_element = etree.Element(element.tag)
                processed_element.text = ''
                for child in element.iter():
                    if child.tag in potential_tags:
                        processed_child = handle_textnode(child, comments_fix=False)
                        if processed_child is not None:
                            # paragraph, append text
                            if child.tag == 'p':
                                if processed_child.text is not None:
                                    processed_element.text = processed_element.text + processed_child.text
                                if processed_child.tail is not None:
                                    processed_element.text = processed_element.text + ' ' + processed_child.tail
                            # handle spaces
                            elif child.tag == 'lb':
                                # delete if empty paragraph so far
                                if len(processed_element.text) < 1:
                                    if child.tail is not None:
                                        processed_element.text = child.tail
                                    child.tag = 'done'
                                else:
                                    newsub = etree.SubElement(processed_element, 'lb')
                                    newsub.tail = child.tail # handle_subelement(child).tail
                            else:
                                newsub = etree.SubElement(processed_element, child.tag)
                                newsub.text = trim(processed_child.text)
                                newsub.tail = trim(processed_child.tail)
                        # print(processed_element.tag, processed_element.text)
                        child.tag = 'done'
                # finish
                #if len(processed_element) > 0:
                if len(processed_element.text) > 0:
                    result_body.append(processed_element)
            # insert it directly
            elif element.tag == 'lb':
                if element.tail is not None and re.search(r'\w+', element.tail):
                    element.tail = trim(element.tail)
                    result_body.append(element)
            # other elements (div, ??, ??)
            else:
                ## delete unwanted
                if element.tag not in potential_tags:
                    # LOGGER.debug('discarding: %s %s', element.tag, element.text)
                    continue
                if element.tag != 'div':
                    LOGGER.warning('processing other element: %s', element.tag)
                processed_element = handle_textnode(element, comments_fix=False)
                if processed_element is not None:
                    element.attrib.clear()
                    # small div-correction # could be moved elsewhere
                    if element.tag == 'div':
                        element.tag = 'p'
                    # insert
                    result_body.append(processed_element)
        # control
        if len(result_body) > 0: # if it has children
            LOGGER.debug(expr)
            break

    # try parsing wild <p> elements
    if len(result_body) == 0: # no children
        LOGGER.debug('Taking all p-elements')
        # prune
        search_tree = discard_unwanted(tree)
        # print(html.tostring(tree, pretty_print=False, encoding='unicode'))
        for element in search_tree.xpath('//p'): # search_tree.xpath('//p')
            # print(element.tag, element.text)
            processed_element = handle_textnode(element, comments_fix=False)
            if processed_element is not None:
                processed_element.attrib.clear()
                processed_element.tail = ''
                result_body.append(processed_element)

    # try parsing tables
    # for _, element in etree.iterparse(xml_file, tag='a'):
    if include_tables is True: #len(result_body) == 0: # no children
        LOGGER.debug('Using table extraction')
        search_tree = discard_unwanted(tree)
        for table_elem in search_tree.xpath('//table'):
            # print(html.tostring(table_elem))
            # iterate through elements in table
            for subelement in table_elem.xpath('.//*'):
                subelement.attrib.clear()
                #if subelement.text is not None:
                #    subelement.text = re.sub(r'(?<![p{P}>])\n', ' ', subelement.text)
                subelement.tail = ''
                if subelement.tag == 'th':
                    subelement.tag = 'head'
                elif subelement.tag == 'tr':
                    subelement.tag = 'row'
                    rowtext = subelement.text # ' '.join(subelement.itertext())
                    if rowtext is None or len(rowtext) < 50:
                        subelement.getparent().remove(subelement)
                        continue
                    subelement.text = trim(rowtext)
                elif subelement.tag == 'td':
                    #if len(' '.join(subelement.itertext())) < 30:
                        #subelement.getparent().remove(subelement)
                        #continue
                    #if subelement.text is not None:
                    subelement.text = trim(subelement.text)
                    subelement.tag = 'cell'
                else:
                    # subelement.getparent().remove(subelement)
                    etree.strip_tags(table_elem, subelement.tag)
            # insert
            if len(' '.join(table_elem.itertext())) > MIN_EXTRACTED_SIZE:
                table_elem.attrib.clear()
                for element in table_elem.iter():
                    if not re.search(r'[p{L}]+', ''.join(element.itertext())):
                        element.clear()
                        # element.getparent().remove(element)
                # prune recursively empty elements
                context = etree.iterwalk(table_elem)
                for _, elem in context:
                    parent = elem.getparent()
                    if recursively_empty(elem):
                        parent.remove(elem)
                result_body.append(table_elem)
    # filter output
    etree.strip_elements(result_body, 'done')
    etree.strip_tags(result_body, 'div')
    # return
    return result_body


#@profile
def extract_comments(tree, include_comments):
    '''Try and extract comments out of potential sections in the HTML'''
    comments_body = etree.Element('body')
    # define iteration strategy
    potential_tags = set(TAG_CATALOG) # 'span'
    ## potential_tags.add('div') trouble with <div class="comment-author meta">
    # LOGGER.debug(sorted(potential_tags))
    ## return comments_body, tree
    for expr in COMMENTS_XPATH:
        # select tree if the expression has been found
        subtree = tree.xpath(expr)
        if len(subtree) == 0:
            continue
        subtree = subtree[0]
        # prune
        subtree = discard_unwanted_comments(subtree)
        # extract content
        for elem in subtree.xpath('.//*'): # was: for elem in tree.xpath(expr):
            if elem.tag in potential_tags: # TAG_CATALOG:
                # print(elem.tag, elem.text_content())
                processed_element = handle_textnode(elem, comments_fix=True)
                # test length and remove
                if processed_element is None or processed_element.text in COMMENTS_BLACKLIST:
                    # elem.getparent().remove(elem)
                    continue
                ## text filter, insert if words? ## ^Pingback
                #if textfilter(elem) is True:
                #    continue
                elem.attrib.clear()
                comments_body.append(elem)
        # control
        if len(comments_body) > 0: # if it has children
            LOGGER.debug(expr)
            # remove corresponding subtree
            #for subtree in tree.xpath(expr):
            subtree.getparent().remove(subtree)
            break
    if include_comments is False:
        comments_body = etree.Element('body')
    return comments_body, tree


def extract_metadata(tree):
    '''Extract title and document date if available/required'''
    try:
        doctitle = tree.find('//title').text
    except AttributeError: # no title found
        doctitle = None
    if DATE_FLAG is True:
        docdate = find_date(tree, extensive_search=False)
    else:
        docdate = None
    return doctitle, docdate


#@profile
def extract(filecontent, url=None, record_id='0001', no_fallback=False, include_comments=True, xml_output=False, tei_output=False, tei_validation=False, target_language=None, include_tables=True):
    '''Main process for text extraction'''
    # init
    global LRU_TEST
    tree = load_html(filecontent)
    if tree is None:
        return None
    # LOGGER.debug('HTML tree loaded for URL: %s', url)
    # print(html.tostring(tree, pretty_print=False, encoding='unicode'))

    # Metadata here
    if xml_output is True or tei_output is True:
        doctitle, docdate = extract_metadata(tree)
    else:
        doctitle = docdate = None

    # clean
    cleaned_tree = manual_cleaning(tree, include_tables)
    # save space and processing time
    cleaned_tree = prune_html(cleaned_tree)
    # use LXML cleaner
    cleaned_tree = HTML_CLEANER.clean_html(cleaned_tree)
    #tree_cache[cleaned_tree] = list(cleaned_tree.iter())
    # bypass
    # cleaned_tree = tree
    # print(html.tostring(cleaned_tree, pretty_print=False, encoding='unicode'))

    ## convert tags, the rest does not work without conversion
    # if tei_output is True:
    cleaned_tree = convert_tags(cleaned_tree)
    # remove hi-element to avoid tail bug
    etree.strip_tags(cleaned_tree, 'hi')

    # comments first, then remove
    commentsbody, cleaned_tree = extract_comments(cleaned_tree, include_comments)

    ## extract content
    temppost_hand = extract_content(cleaned_tree, include_tables)

    ## compare
    temp_text = u' '.join(temppost_hand.itertext())
    if no_fallback is False and 0 <= len(temp_text) < 300:
        # try with justext
        temppost_algo = try_justext(tree, url) # cleaned_tree
        # compare
        temp_jt = u' '.join(temppost_algo.itertext())
        LOGGER.info('extracted length: %s (jusText) %s (extraction)', len(temp_jt), len(temp_text))
        # conditions to use justext
        if 0 <= len(temp_text) < 300 and len(temp_jt) > 2*len(temp_text):
            justext_flag = True
        elif len(temppost_hand.xpath('//p')) == 0 and len(temp_jt) > 0: # borderline case
            justext_flag = True
        else:
            justext_flag = False
        if justext_flag is True: # was len(temp_text) > 10
            postbody = temppost_algo
            LOGGER.info('using justext: %s', url)
        else:
            postbody = temppost_hand
            LOGGER.info('using custom extraction: %s', url)
    else:
        LOGGER.info('extracted length: %s (extraction)', len(temp_text))
        postbody = temppost_hand
        temp_jt = ''

    # try to use original/dirty tree
    if len(temp_text) == 0 and len(temp_jt) == 0:
        tree = convert_tags(tree)
        temppost_hand = extract_content(tree)
        temp_text = u' '.join(temppost_hand.itertext())
        LOGGER.debug('non-clean extracted length: %s (extraction)', len(temp_text))
        postbody = temppost_hand

    # sanity check on length
    temp_text = u' '.join(postbody.itertext())
    temp_comments = u' '.join(commentsbody.itertext())
    if len(temp_text) < MIN_EXTRACTED_SIZE:
        LOGGER.error('not enough text %s %s', record_id, url)
    if len(temp_comments) < MIN_EXTRACTED_COMM_SIZE:
        LOGGER.warning('not enough comments %s %s', record_id, url)
    if len(temp_text) < MIN_EXTRACTED_SIZE and len(temp_comments) < MIN_EXTRACTED_COMM_SIZE:
        LOGGER.info('text and comments not long enough: %s %s', len(temp_text), len(temp_comments))
        return None

    # sanity check on language
    if target_language is not None:
        if LANGID_FLAG is True:
            # comments
            if len(temp_comments) > len(temp_text):
                langtest = temp_comments
            # default
            else:
                langtest = temp_text
            langresult = langid.classify(langtest)
            if langresult[0] != target_language:
                LOGGER.warning('wrong language: %s %s %s', langresult, record_id, url)
                LOGGER.debug('wrong language: %s %s', langresult, temp_text)
                return None
        else:
            LOGGER.warning('package not installed, cannot perform language identification')

    # cache elements
    if LRU_FLAG is True:
        cache(postbody)
        cache(commentsbody)
    #del tree_cache[cleaned_tree]

    # XML (TEI) steps
    if include_comments is False:
        commentsbody = None
    if tei_output is True:
        # build TEI tree
        output = write_teitree(postbody, commentsbody, url, doctitle, docdate)
        # filter output (strip unwanted elements), just in case
        # check and repair
        output = check_tei(output, url)
        # validate
        # why is it necessary?
        testtree = etree.fromstring(etree.tostring(output))
        if tei_validation is True:
            result = validate_tei(testtree)
            LOGGER.info('TEI validation result: %s %s %s', result, record_id, url)
    else:
        output = etree.Element('doc')
        postbody.tag = 'main'
        output.append(postbody)
        if commentsbody is not None:
            commentsbody.tag = 'comments'
            output.append(commentsbody)
        # url in xml
        if url is not None:
            output.set('source', url)
        if doctitle is not None:
            output.set('title', doctitle)
        if docdate is not None:
            output.set('date', docdate)

    # sanity check on markup
    # if re.search(r'\[url', u''.join(postbody.itertext()):

    # check duplicates at body level
    teststring = ' '.join(postbody.itertext()).encode('utf-8')
    if LRU_FLAG is True and LRU_TEST.has_key(teststring) is True:
        # LRU_TEST[teststring] = 1
        return None
    if LRU_FLAG is False and teststring in LRU_TEST:
        # LRU_TEST[teststring] = 1
        return None

    if xml_output is False and tei_output is False:
        returnstring = xmltotxt(output)
    else:
        control_string = etree.tostring(output)
        control_parser = etree.XMLParser(remove_blank_text=True)
        output_tree = etree.fromstring(control_string, control_parser)
        returnstring = etree.tostring(output_tree, pretty_print=True, encoding='unicode')
        # xml_declaration=True,

        ##  garbled unicode
        #try:
        #    returnstring = ftfy.fix_text(returnstring, fix_entities=False, fix_encoding=True, fix_surrogates=True)
        #except UnicodeDecodeError as err:
        #    LOGGER.warning('Unicode error: %s %s', err, record_id)


    returnstring = sanitize(returnstring)
    return returnstring


# for legacy and backwards compatibility
process_record = extract


#def custom_justext(htmldom):
#    paragraphs = ParagraphMaker.make_paragraphs(htmldom)
#    justext.classify_paragraphs(paragraphs, justext.get_stoplist("German"), length_low=LENGTH_LOW_DEFAULT, \
#        length_high=LENGTH_HIGH_DEFAULT, stopwords_low=STOPWORDS_LOW_DEFAULT, \
#        stopwords_high=STOPWORDS_HIGH_DEFAULT, max_link_density=MAX_LINK_DENSITY_DEFAULT, no_headings=NO_HEADINGS_DEFAULT)
#    justext.revise_paragraph_classification(paragraphs, max_heading_distance=MAX_HEADING_DISTANCE_DEFAULT)
#    return paragraphs
