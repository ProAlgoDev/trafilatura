# pylint:disable-msg=E0611,I1101
"""
Module bundling all functions needed to extract the text in a webpage.
"""

## This file is available from https://github.com/adbar/trafilatura
## under GNU GPL v3 license


# standard
import logging
import re # import regex as re

from copy import deepcopy

from lxml import etree, html

# own
from .external import justext_rescue, try_readability
from .filters import duplicate_test, language_filter, put_in_cache
from .htmlprocessing import (convert_tags, discard_unwanted,
                             discard_unwanted_comments, handle_textnode,
                             link_density_test,
                             manual_cleaning, process_node, prune_html)
from .metadata import extract_metadata
from .settings import (HTML_CLEANER, MIN_EXTRACTED_SIZE, MIN_EXTRACTED_COMM_SIZE,
                       MIN_OUTPUT_SIZE, MIN_OUTPUT_COMM_SIZE, TAG_CATALOG)
from .utils import load_html, sanitize, trim, txttocsv
from .xml import add_xml_meta, build_tei_output, build_xml_output, validate_tei, xmltotxt
from .xpaths import BODY_XPATH, COMMENTS_XPATH


LOGGER = logging.getLogger(__name__)


def sanitize_tree(tree):
    '''Sanitize the output from the generic algorithm'''
    # cleaned_tree = manual_cleaning(tree, True)
    # cleaned_tree = HTML_CLEANER.clean_html(cleaned_tree)
    etree.strip_tags(tree, 'div')
    tree = prune_html(tree)
    #for elem in tree.iter():
    #    elem.attrib.clear()
    cleaned_tree = convert_tags(tree)
    for elem in cleaned_tree.iter():
        #if elem.tag in ('code', 'del', 'head', 'hi', 'item', 'p', 'quote'):
        #    if elem.text is None or elem.text.isspace():
        #        elem.getparent().remove(elem)
        #        continue
        #if elem.text:
        elem.text = sanitize(elem.text)
        #if elem.tail:
        elem.tail = sanitize(elem.tail)
    # cleaned_tree = prune_html(cleaned_tree)
    return cleaned_tree


def handle_titles(element):
    '''Process head elements (titles)'''
    # maybe needs attention
    if element.tail and re.search(r'\w', element.tail):
        LOGGER.debug('tail in title, stripping: %s', element.tail)
    element.tail = None
    title = process_node(element)
    if title is not None and title.text and re.search(r'\w', title.text):
        return title
    return None


def handle_formatting(element):
    '''Process formatting elements (b, i, etc. converted to hi) found
       outside of paragraphs'''
    processed_element = None
    if element.text is not None or element.tail is not None:
        processed_element = etree.Element('p')
        processed_child = etree.SubElement(processed_element, element.tag)
        if element.text is not None and not element.text.isspace():
            processed_child.text = trim(element.text)
        if element.tail is not None and not element.text.isspace():
            processed_child.tail = trim(element.tail)
    return processed_element


def handle_lists(element):
    '''Process lists elements'''
    processed_element = etree.Element(element.tag)
    for child in element.iter('item'):
        newchildelem = etree.Element('item')
        if len(child) == 0:
            processed_child = process_node(child)
            if processed_child is not None:
                # processed_element.append(deepcopy(processed_child))
                # childelem = etree.SubElement(processed_element, processed_child.tag)
                newchildelem.text = processed_child.text
                newchildelem.tail = processed_child.tail
                processed_element.append(newchildelem)
        else:
            # print(child.tag, child.text, child.tail)
            # proceed with iteration, fix for nested elements
            for subelem in child.iter():
                # newsub = etree.Element('item')
                processed_subchild = handle_textnode(subelem, comments_fix=False)  # process_node(subelem)
                # add child element to processed_element
                if processed_subchild is not None:
                    subchildelem = etree.SubElement(newchildelem, processed_subchild.tag)
                    subchildelem.text = processed_subchild.text
                    subchildelem.tail = processed_subchild.tail
                    # newsub.append(deepcopy(processed_subchild))
                    # processed_element.append(processed_subchild)
                subelem.tag = 'done'
            etree.strip_tags(newchildelem, 'item')
        if newchildelem.text or len(newchildelem) > 0:
            processed_element.append(newchildelem)
        child.tag = 'done'
    # avoid double tags??
    if len(processed_element) > 0:  # if it has children
        # test if it has text
        teststring = ''.join(processed_element.itertext())
        if teststring and re.search(r'\S', teststring):
            return processed_element
    return None


def handle_quotes(element):
    '''Process quotes elements'''
    processed_element = etree.Element(element.tag)
    for child in element.iter():
        processed_child = process_node(child) # handle_textnode(child, comments_fix=True)
        if processed_child is not None:
            # processed_element.append(deepcopy(processed_child))
            newsub = etree.SubElement(processed_element, child.tag)
            newsub.text = processed_child.text
            newsub.tail = processed_child.tail
        child.tag = 'done'
    if len(processed_element) > 0:
        # avoid double/nested tags
        etree.strip_tags(processed_element, 'quote')
        # test if it has text
        # teststring = ''.join(processed_element.itertext())
        # if len(teststring) > 0 and re.search(r'[p{L}]', teststring):
        return processed_element
    return None


def handle_other_elements(element, potential_tags):
    '''Handle diverse or unknown elements in the scope of relevant tags'''
    # delete unwanted
    if element.tag not in potential_tags:
        # LOGGER.debug('discarding: %s %s', element.tag, element.text)
        return None
    if element.tag == 'div':
        processed_element = handle_textnode(element, comments_fix=False)  # process_node(element)
        if processed_element is not None:
            processed_element.attrib.clear()
            # small div-correction # could be moved elsewhere
            if processed_element.tag == 'div':
                processed_element.tag = 'p'
            # insert
            return processed_element
    else:
        LOGGER.debug('processing other element: %s %s', element.tag, element.text)
    return None


def handle_paragraphs(element, potential_tags):
    '''Process paragraphs (p) elements along with their children,
       trim and clean the content'''
    element.attrib.clear()
    #etree.strip_tags(element, 'p')  # change in precision
    # no children
    if len(element) == 0:
        processed_element = process_node(element)  # handle_textnode(element, comments_fix=False)
        if processed_element is not None:
            return processed_element
        return None
    # children
    processed_element = etree.Element(element.tag)
    for child in element.iter():
        if child.tag not in potential_tags:
            LOGGER.debug('unexpected elem in paragraph: %s %s %s', child.tag, child.text, child.tail)
            continue
        processed_child = handle_textnode(child, comments_fix=False)
        if processed_child is not None:
            # needing attention!
            if child.tag == 'p':
                LOGGER.debug('extra elem within p: %s %s %s', child.tag, child.text, child.tail)
                processed_element.text = trim(' ' + child.text)
                continue
            newsub = etree.Element(child.tag)
            # handle formatting
            if child.tag == 'hi':
                # check depth and clean
                if len(child) > 0:
                    for item in child:  # children are lists
                        if item.text is not None and not item.text.isspace():
                            item.text = ' ' + item.text
                        etree.strip_tags(child, item.tag)
                newsub.set('rend', child.get('rend'))
            # handle line breaks
            elif child.tag == 'lb':
                try:
                    processed_child.tail = process_node(child).tail
                except AttributeError:  # no text
                    pass
            # prepare text
            if processed_child.text is None or processed_child.text.isspace():
                processed_child.text = ''
            # if there are already children
            if len(processed_element) > 0:
                if processed_child.tail is not None and not processed_child.tail.isspace():
                    newsub.tail = processed_child.text + processed_child.tail
                else:
                    newsub.tail = processed_child.text
            else:
                newsub.text = processed_child.text
                newsub.tail = processed_child.tail
            processed_element.append(newsub)
            child.tag = 'done'
    # finish
    if len(processed_element) > 0 or processed_element.text:
        # clean trailing lb-elements
        if len(processed_element) > 0 and processed_element[-1].tag == 'lb' and processed_element[-1].tail is None:
            processed_element[-1].getparent().remove(processed_element[-1])
        return processed_element
    LOGGER.debug('discarding p-child: %s', html.tostring(processed_element))
    return None


def handle_table(table_elem):
    '''Process single table element'''
    newtable = etree.Element('table')
    newrow = etree.Element('row')
    i = 0
    # explore sub-elements
    for subelement in table_elem.iter():
        i += 1
        if subelement.tag == 'tr':
            # process existing row
            if len(newrow) > 0:
                newtable.append(newrow)
                newrow = etree.Element('row')
            # skip rows empty of text
            #textcontent = ''.join(subelement.itertext())
            #if len(textcontent) == 0 or not re.search(r'[p{L}]+', textcontent):
            #    continue
        elif subelement.tag in ('td', 'th'):
            # process
            processed_cell = process_node(subelement)
            if processed_cell is None or processed_cell.text is None or not processed_cell.text:
                continue
            # define tag
            newsub = etree.SubElement(newrow, 'cell')
            if subelement.tag == 'th':
                newsub.set('role', 'head')
            newsub.text = processed_cell.text
            #newrow.append(newsub)
        # beware of nested tables
        elif subelement.tag == 'table' and i > 1:
            break
    # end of processing
    if len(newrow) > 0:
        newtable.append(newrow)
    if len(newtable) > 0:
        return newtable
    return None


def recover_wild_paragraphs(tree, result_body, potential_tags=TAG_CATALOG):
    '''Look for all p-elements, including outside of the determined frame
       and throughout the document to recover potentially missing text parts'''
    LOGGER.debug('Taking all p-elements')
    # prune
    search_tree = discard_unwanted(tree)
    etree.strip_tags(search_tree, 'a', 'link', 'span')
    processed_elems = [handle_paragraphs(element, potential_tags) for element in search_tree.iter('blockquote', 'code', 'p', 'pre', 'q', 'quote')] # 'head', 'list'
    result_body.extend(list(filter(None.__ne__, processed_elems)))
    return result_body


def handle_textelem(element, potential_tags):
    '''Process text element and determine how to deal with its content'''
    new_element = None
    # bypass: nested elements
    if element.tag == 'list':
        new_element = handle_lists(element)
    elif element.tag == 'quote':   # + 'code'?
        new_element = handle_quotes(element)
    elif element.tag == 'head':
        new_element = handle_titles(element)
    elif element.tag == 'p':
        new_element = handle_paragraphs(element, potential_tags)
    elif element.tag == 'lb':
        if element.tail is not None and not element.tail.isspace():
            new_element = etree.Element('p')
            new_element.text = process_node(element).tail
            # new_element.text = handle_textnode(element, comments_fix=False).tail
    elif element.tag == 'hi':
        new_element = handle_formatting(element)
    elif element.tag == 'table' and 'table' in potential_tags:
        new_element = handle_table(element)
    else:
        # other elements (div, ??, ??)
        new_element = handle_other_elements(element, potential_tags)
    return new_element


def extract_content(tree, include_tables=False):
    '''Find the main content of a page using a set of XPath expressions,
       then extract relevant elements, strip them of unwanted subparts and
       convert them'''
    sure_thing = False
    result_body = etree.Element('body')
    # iterate
    for expr in BODY_XPATH:
        # select tree if the expression has been found
        subtree = tree.xpath(expr)
        if not subtree:
            continue
        subtree = subtree[0]
        # prune
        subtree = discard_unwanted(subtree)
        # remove elements by link density
        for elem in subtree.iter('list'):
            if link_density_test(elem) is True:
                elem.getparent().remove(elem)
                continue
            elem.attrib.clear()
            #for subelem in elem.iter('item'):
            #    subelem.attrib.clear()
        etree.strip_tags(subtree, 'a', 'link', 'span')
        # define iteration strategy
        potential_tags = set(TAG_CATALOG)  # + 'span'?
        if include_tables is True:
            potential_tags.add('table')
        # no paragraphs containing text
        if not subtree.xpath('//p//text()'):
            potential_tags.add('div')
        LOGGER.debug(sorted(potential_tags))
        # etree.strip_tags(subtree, 'lb') # BoingBoing-Bug
        # print(html.tostring(subtree, pretty_print=True, encoding='unicode'))
        # extract content
        processed_elems = [handle_textelem(e, potential_tags) for e in subtree.xpath('.//*')]
        # list(filter(None.__ne__, processed_elems))
        result_body.extend([e for e in processed_elems if e is not None])
        # exit the loop if the result has children
        if len(result_body) > 0:
            sure_thing = True
            LOGGER.debug(expr)
            break
    # try parsing wild <p> elements if nothing found or text too short
    temp_text = trim(' '.join(result_body.itertext()))
    len_text = len(temp_text)
    if len(result_body) == 0 or len_text < MIN_EXTRACTED_SIZE:
        result_body = recover_wild_paragraphs(tree, result_body)
        #search_tree = discard_unwanted(tree)
        #search_tree = prune_html(search_tree)
        #result_body, _, _ = baseline(search_tree)
        temp_text = trim(' '.join(result_body.itertext()))
        len_text = len(temp_text)
    # filter output
    etree.strip_elements(result_body, 'done')
    etree.strip_tags(result_body, 'div')
    # return
    return result_body, temp_text, len_text, sure_thing


def process_comments_node(elem, potential_tags):
    '''Process comment node and determine how to deal with its content'''
    if elem.tag in potential_tags:
        # print(elem.tag, elem.text_content())
        processed_element = handle_textnode(elem, comments_fix=True)
        # test length and remove
        if processed_element is not None: # and processed_element.text not in COMMENTS_BLACKLIST:
            processed_element.attrib.clear()
            # if textfilter(elem) is True: # ^Pingback
            #    return None
            return processed_element
    return None


def extract_comments(tree):
    '''Try and extract comments out of potential sections in the HTML'''
    comments_body = etree.Element('body')
    # define iteration strategy
    potential_tags = set(TAG_CATALOG)  # 'span'
    # potential_tags.add('div') trouble with <div class="comment-author meta">
    for expr in COMMENTS_XPATH:
        # select tree if the expression has been found
        subtree = tree.xpath(expr)
        if not subtree:
            continue
        subtree = subtree[0]
        # prune
        subtree = discard_unwanted_comments(subtree)
        etree.strip_tags(subtree, 'a', 'link', 'span')
        # extract content
        #for elem in subtree.xpath('.//*'):
        #    processed_elem = process_comments_node(elem, potential_tags)
        #    if processed_elem is not None:
        #        comments_body.append(processed_elem)
        processed_elems = [process_comments_node(elem, potential_tags) for elem in subtree.xpath('.//*')]
        comments_body.extend(list(filter(None.__ne__, processed_elems)))
        # control
        if len(comments_body) > 0:  # if it has children
            LOGGER.debug(expr)
            # remove corresponding subtree
            subtree.getparent().remove(subtree)
            break
    # lengths
    temp_comments = trim(' '.join(comments_body.itertext()))
    return comments_body, temp_comments, len(temp_comments), tree


def compare_extraction(tree, backup_tree, url, body, text, len_text, target_language):
    '''Decide whether to choose own or external extraction
       based on a series of heuristics'''
    #if tree is None:
    #    return body, text, len_text
    # try with readability
    temppost_algo = try_readability(backup_tree, url)
    algo_text = trim(' '.join(temppost_algo.itertext()))
    len_algo = len(algo_text)
    # compare
    LOGGER.debug('extracted length: %s (algorithm) %s (extraction)', len_algo, len_text)
    # conditions to use alternative algorithms
    if len_algo == 0 or len_algo == len_text:
        algo_flag = False
    elif len_text == 0 and len_algo > 0:
        algo_flag = True
    elif len_text > 2*len_algo:
        algo_flag = False
    elif len_algo > 2*len_text: # or 0.95*len_text < len_algo < 0.99*len_text:
        algo_flag = True
    elif not body.xpath('//p//text()') and len_algo > 0:
        algo_flag = True  # borderline case
    else:
        # print(sure_thing, len_text, len_algo)
        LOGGER.debug('extraction values: %s %s for %s', len_text, len_algo, url)
        algo_flag = False
    # apply decision
    if algo_flag is True:
        body = sanitize_tree(temppost_algo)
        text = algo_text
        len_text = len_algo
        LOGGER.info('using generic algorithm: %s', url)
    else:
        LOGGER.info('using custom extraction: %s', url)
    # override faulty extraction
    if body.xpath('//aside|//button|//figure|//footer|//img|//input|//noscript|//svg'):
        body2, len_text2, text2 = justext_rescue(tree, url, target_language, body, 0, '')
        LOGGER.debug('justext length %s', len_text2)
        if len_text2 > 0: #MIN_EXTRACTED_SIZE:
            body, len_text, text = body2, len_text2, text2
        else:
            # post-processing: remove unwanted sections
            for elem in body.xpath('//aside|//button|//figure|//footer|//img|//input|//link|//nav|//noscript|//svg|//time'):
                elem.getparent().remove(elem) # elem.drop_tree()
            # clean and converttree
            etree.strip_tags(body, 'article', 'span') # 'header', 'section', ...
            body = convert_tags(body)
    # try with justext
    elif len_text < MIN_EXTRACTED_SIZE:
        LOGGER.error('not enough text %s', url)  # record_id,
        body, len_text, text = justext_rescue(tree, url, target_language, body, len_text, text)
        LOGGER.debug('justext length %s', len_text)
    # second backup
    #if len_text < MIN_EXTRACTED_SIZE:
    #     body2, len_text2, temp_text2 = baseline(backup_tree)
    #     if len_text2 > MIN_EXTRACTED_SIZE:
    #         body, text, len_text = body2, len_text2, temp_text2
    return body, text, len_text


def baseline(filecontent):
    """Use baseline extraction function targeting JSON metadata and/or text paragraphs"""
    tree = load_html(filecontent)
    postbody = etree.Element('body')
    if tree is None:
        return postbody, 0, ''
    # scrape from json text
    for elem in tree.xpath('//script[@type="application/ld+json"]'):
        if elem.text and '"articleBody":' in elem.text:
            mymatch = re.search(r'"articleBody":"(.+?)","', elem.text)
            if mymatch:
                temp_text = mymatch.group(1).replace('\\"', '"')
                postbody = etree.Element('body')
                elem = etree.Element('p')
                elem.text = temp_text
                postbody.append(elem)
                # temp_text = trim(temp_text)
                return postbody, len(temp_text), temp_text
    # scrape from article tag
    elems = tree.xpath('//article') # |//main
    if elems:  # len(elems) > 0:
        article_elem = elems[0]
        temp_text = sanitize(article_elem.text_content())
        len_text = len(temp_text)
        if len_text > 0:
            elem = etree.Element('p')
            elem.text = temp_text
            postbody.append(elem)
            return postbody, len_text, temp_text
    # scrape from text paragraphs
    results = set()
    resultlist = []
    # search_tree = discard_unwanted(tree)
    # search_tree = prune_html(tree)
    for element in tree.iter('blockquote', 'code', 'p', 'pre', 'q', 'quote'):
        entry = element.text_content()
        if entry not in results:
            resultlist.append(entry)
        results.add(entry)
    for textpart in resultlist:
        elem = etree.Element('p')
        elem.text = textpart
        postbody.append(elem)
    temp_text = sanitize('\n'.join(postbody.itertext()))
    return postbody, len(temp_text), temp_text


def determine_returnstring(docmeta, postbody, commentsbody, output_format, tei_validation, record_id):
    '''Convert XML tree to chosen format, clean the result and output it as a string'''
    # XML (TEI) steps
    if 'xml' in output_format:
        # last cleaning
        for element in postbody.iter():
            if len(element) == 0 and not element.text and not element.tail:
                parent = element.getparent()
                if parent:
                    parent.remove(element)
        # build output trees
        if output_format == 'xml':
            output = build_xml_output(postbody, commentsbody)
            output = add_xml_meta(output, docmeta)
        elif output_format == 'xmltei':
            output = build_tei_output(postbody, commentsbody, docmeta)
        # can be improved
        control_string = etree.tostring(output, encoding='unicode')
        control_string = sanitize(control_string)
        # necessary for cleaning
        control_parser = etree.XMLParser(remove_blank_text=True)
        output_tree = etree.fromstring(control_string, control_parser)
        # validate
        if output_format == 'xmltei' and tei_validation is True:
            result = validate_tei(output_tree)
            LOGGER.info('TEI validation result: %s %s %s', result, record_id, docmeta.url)
        # output as string
        returnstring = etree.tostring(output_tree, pretty_print=True, encoding='unicode').strip()
    # CSV + TXT output
    else:
        if output_format == 'csv':
            posttext = xmltotxt(postbody)
            if commentsbody is not None:
                commentstext = xmltotxt(commentsbody)
            else:
                commentstext = ''
            returnstring = txttocsv(posttext, commentstext, docmeta)
        else:
            output = build_xml_output(postbody, commentsbody)
            returnstring = xmltotxt(output)
    return returnstring


def map_format(output_format, csv_output, xml_output, tei_output):
    '''Map existing options to format choice.'''
    if csv_output is True:
        output_format = 'csv'
    elif xml_output is True:
        output_format = 'xml'
    elif tei_output is True:
        output_format = 'xmltei'
    return output_format


def extract(filecontent, url=None, record_id='0001', no_fallback=False,
            include_comments=True, output_format='txt',
            csv_output=False, xml_output=False, tei_output=False,
            tei_validation=False, target_language=None,
            include_tables=True, include_formatting=False,
            date_extraction_params=None):
    '''Main process for text extraction'''
    # temporary metadata mapping
    output_format = map_format(output_format, csv_output, xml_output, tei_output)
    # load data
    tree = load_html(filecontent)
    if tree is None:
        return None
    # backup (or not) for further processing
    if no_fallback is False:
        backup_tree = deepcopy(tree)
    else:
        backup_tree = None

    # Metadata here
    if output_format != 'txt':
        docmeta = extract_metadata(tree, url, date_extraction_params)
    else:
        docmeta = None

    # clean
    cleaned_tree = manual_cleaning(tree, include_tables)
    # save space and processing time
    cleaned_tree = prune_html(cleaned_tree)
    # use LXML cleaner
    cleaned_tree = HTML_CLEANER.clean_html(cleaned_tree)

    # convert tags, the rest does not work without conversion
    cleaned_tree = convert_tags(cleaned_tree)
    # remove hi-element to avoid tail bug
    if 'xml' not in output_format or include_formatting is False:
        etree.strip_tags(cleaned_tree, 'hi')

    # comments first, then remove
    if include_comments is True:
        commentsbody, temp_comments, len_comments, cleaned_tree = extract_comments(cleaned_tree)
    else:
        commentsbody, temp_comments, len_comments = None, '', 0

    # extract content
    postbody, temp_text, len_text, sure_thing = extract_content(cleaned_tree, include_tables)

    # compare if necessary
    if no_fallback is False:
        #if sure_thing is False:
        postbody, temp_text, len_text = compare_extraction(tree, backup_tree, url, postbody, temp_text, len_text, target_language)
    else:
        # rescue: try to use original/dirty tree
        if sure_thing is False and len_text < MIN_EXTRACTED_SIZE:
            postbody, len_text, temp_text = baseline(filecontent)
            #tree = load_html(filecontent)
            #tree = convert_tags(tree)
            #postbody, temp_text, len_text, sure_thing = extract_content(tree)
            LOGGER.debug('non-clean extracted length: %s (extraction)', len_text)

    # size checks
    if len_comments < MIN_EXTRACTED_COMM_SIZE:
        LOGGER.info('not enough comments %s %s', record_id, url)
    if len_text < MIN_OUTPUT_SIZE and len_comments < MIN_OUTPUT_COMM_SIZE:
        LOGGER.info('text and comments not long enough: %s %s', len_text, len_comments)
        return None

    # check duplicates at body level
    if duplicate_test(postbody) is True:
        return None

    # sanity check on language
    if language_filter(temp_text, temp_comments, target_language, record_id, url) is True:
        return None

    # cache elements
    put_in_cache(postbody)
    if commentsbody is not None:
        put_in_cache(commentsbody)

    returnstring = determine_returnstring(docmeta, postbody, commentsbody, output_format, tei_validation, record_id)
    return returnstring


# for legacy and backwards compatibility
process_record = extract
