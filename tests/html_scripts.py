"""The <script> elements of a page or template, found by the standard library's parser.

A regex over the markup is a tag filter, and the usual one misses `<SCRIPT>` and
`</script >` — CodeQL flags it for that. The parser handles both, and says where each
element sits in the source, so the tests can lift the scripts out or cut them away.
"""

from html.parser import HTMLParser


class Script:
    def __init__(self, attrs, body, start, end):
        self.attrs = attrs  # {name: value} off the opening tag
        self.body = body  # the source between the tags, as written
        self.start, self.end = start, end  # the whole element, as offsets in the page


class _Finder(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.scripts = []
        self._line_starts = [0]
        for line in html.split("\n"):
            self._line_starts.append(self._line_starts[-1] + len(line) + 1)
        self._open = None

    def _offset(self):
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            start = self._offset()
            opening = self.get_starttag_text()
            assert opening is not None  # only None outside a start tag
            self._open = (dict(attrs), start, start + len(opening))

    def handle_endtag(self, tag):
        if tag == "script" and self._open:
            attrs, start, body_start = self._open
            body_end = self._offset()
            end = self.html.index(">", body_end) + 1
            body = self.html[body_start:body_end]
            self.scripts.append(Script(attrs, body, start, end))
            self._open = None


def scripts(html):
    """Every <script> element in `html`, in document order."""
    finder = _Finder(html)
    finder.feed(html)
    finder.close()
    return finder.scripts


def without_scripts(html):
    """`html` with every <script> element cut out, tags and all."""
    out, at = [], 0
    for s in scripts(html):
        out.append(html[at : s.start])
        at = s.end
    out.append(html[at:])
    return "".join(out)
