import pytest
from src.source_ocr import align_fragment, locate_pages, ordered_lines


def test_alignment_does_not_extrapolate_noise_into_next_paragraph():
    query = 'garbled words It is also much more than following a recipe. In whipping up a tasty gourmet treat xyz 7 5'
    source = 'Previous paragraph.\nCooking is not drudgery. It is also much more than following a recipe.\nIn whipping up a tasty gourmet treat.\nWith the proper attitude, cooking can be relaxing.'
    text, coverage = align_fragment(query, source)
    assert text.startswith('Cooking is not drudgery.')
    assert text.endswith('gourmet treat.')
    assert 'With the proper attitude' not in text
    assert coverage > .28


def test_locates_both_pages_of_a_continued_paragraph():
    first = 'An unusual original paragraph begins with enough distinctive words on this page.'
    second = 'A continuation with several other distinctive words appears on the next page.'
    assert locate_pages(first + ' ' + second, ['Unrelated text', first, second, 'Something else']) == [1, 2]


def test_ambiguous_and_absent_source_are_rejected():
    query = 'These words repeat across very different pages of this document.'
    with pytest.raises(ValueError, match='kilku'):
        locate_pages(query, [query, 'other page', 'other page', query])
    with pytest.raises(ValueError):
        align_fragment('Completely unrelated wording without any evidence whatsoever.', 'An unrelated source paragraph.')


def test_orders_detached_word_boxes_by_line_and_x_coordinate():
    def row(x, y, text):
        return ([[x,y],[x+40,y],[x+40,y+15],[x,y+15]], text, .9)
    lines = ordered_lines([row(100,30,'second'), row(150,10,'world'), row(50,10,'Hello'), row(150,30,'line')])
    assert [r['text'] for r in lines] == ['Hello world', 'second line']


def test_bilingual_heading_spanning_two_source_lines():
    from src.source_ocr import align_short_fragment
    query = '## pros NJLSHR00M POWDER Srzybowy/m$czka grzybowa'
    source = 'Store in air-tight container.\nMUSHROOM POWDER\nproszek grzybowylmączka grzybowa\nThis is commercially available.'
    text, score = align_short_fragment(query, source)
    assert text == 'MUSHROOM POWDER\nproszek grzybowylmączka grzybowa'
    assert score > .7


def test_short_heading_with_multiple_matching_locations_is_rejected():
    from src.source_ocr import align_short_fragment
    with pytest.raises(ValueError, match='kilku'):
        align_short_fragment('## MUSHROOM POWDER', 'MUSHROOM POWDER\nSome unrelated prose.\nMUSHROOM POWDER')
