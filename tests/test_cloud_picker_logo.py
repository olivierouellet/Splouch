"""How the picker page sizes an operator's logo.

The logo used to be capped at `max-width:200px; max-height:120px`, which is a badge
box. Clubs upload banners — wide SVG wordmarks — and a banner in a badge box is a
postage stamp above a page of full-width cards.

The fix is deliberately *not* a new operator setting. The uploaded file already says
which kind it is: a banner is wide, a badge is square. A checkbox would ask the
operator to restate that, and it would have to travel in `/picker/config`, which is
the contract native pickers read (`docs/app.md` `P-05`) — so it would either be
implemented in every app or silently work on the web only.

Instead one rule covers both, and the three properties below are what make it work:

* `width: 100%` + `max-width: var(--column)` — the box is the page's content column,
  so a banner reaches the same edges as the meet cards under it.
* `max-height` — the cap that stops a tall image from owning the page.
* `object-fit: contain` — the load-bearing one. When `max-height` clamps a box whose
  width is already fixed at 100%, the image is stretched to the box unless it is told
  to fit inside it. Without this a 4:1 banner renders smeared across 800×160, and a
  square badge renders as a 800px-wide letterbox.
"""
import os
import re

import pytest

from conftest import matched

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICKER = os.path.join(REPO, 'cloud', 'templates', 'picker.html')


@pytest.fixture(scope='module')
def src():
    return open(PICKER, encoding='utf-8').read()


@pytest.fixture(scope='module')
def rule(src):
    m = re.search(r'\.picker-logo\s*\{(.*?)\}', src, re.S)
    assert m, '.picker-logo rule is gone'
    return ' '.join(m.group(1).split())


def test_the_logo_is_as_wide_as_the_content_column(rule):
    assert 'width: 100%' in rule
    assert 'max-width: var(--column)' in rule, \
        'a literal width here drifts from .meets the first time the column changes'


def test_the_logo_height_is_capped(rule):
    height = re.search(r'max-height:\s*(\d+)px', rule)
    assert height, 'without a cap a square logo fills the column and owns the page'
    assert 100 <= int(height.group(1)) <= 260


def test_the_logo_is_contained_not_stretched(rule):
    """The whole reason one rule can serve a banner and a badge."""
    assert 'object-fit: contain' in rule


def test_the_column_is_one_value_shared_with_the_meet_list(src):
    assert re.search(r'--column:\s*\d+px', src), 'the column width has no single source'
    meets = matched(r'\.meets\s*\{([^}]*)\}', src)
    assert 'max-width: var(--column)' in ' '.join(meets.split()), \
        'the cards and the logo must read the same column, or they stop lining up'


def test_neither_logo_slot_carries_its_own_inline_size(src):
    """There are two — above the title and below it. They drifted as inline styles."""
    imgs = re.findall(r'<img src="/picker_logo"[^>]*>', src)
    assert len(imgs) == 2, 'the two logo slots have changed shape'
    for img in imgs:
        assert 'class="picker-logo"' in img
        assert 'style=' not in img, 'an inline size overrides the shared rule'


def test_no_new_branding_flag_reached_the_client_contract():
    """A logo-width setting here is a contract change; this is what says so out loud.

    If a future change does add one, this test is the place to decide it on purpose:
    update `docs/app.md` `P-05` and the native pickers in the same breath.
    """
    import sys
    sys.path.insert(0, os.path.join(REPO, 'cloud'))
    import tempfile
    os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-picker-test-'))
    import cloud_server as cs
    assert set(cs._picker_branding()) == {'title', 'window_title', 'has_logo', 'logo_above'}
