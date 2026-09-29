// @ts-check
/*
 * foldName(s): the one text fold every search on these pages uses. It serves the
 * swimmer typeahead (docs/app.md `S-09`) and the meet picker's search (`P-17`).
 *
 * Lowercase, decompose, expand, then drop what is left above ASCII, so an accented
 * name stays reachable from plain keys. Every client must fold identically or the
 * same query finds different things per platform.
 *
 * The expansion table is the part that is easy to leave out. These 17 letters have no
 * canonical decomposition, so NFD leaves them whole and the sweep below would delete
 * the letter itself: `Sœurs` folded to `surs`, and nobody types `œ` on a phone. They
 * are expanded after NFD, which also covers their accented forms for free: `ǿ`
 * decomposes to `ø` + acute, and the `ø` is then expanded like any other.
 */
// biome-ignore format: a table, laid out as one so a missing letter shows
var FOLD_LETTERS = {
    'ß': 'ss', 'æ': 'ae', 'ð': 'd',  'ø': 'o', 'þ': 'th', 'đ': 'd', 'ħ': 'h',
    'ı': 'i',  'ĳ': 'ij', 'ĸ': 'k',  'ŀ': 'l', 'ł': 'l',  'ŉ': 'n', 'ŋ': 'n',
    'œ': 'oe', 'ŧ': 't',  'ſ': 's'
};
var FOLD_RE = new RegExp('[' + Object.keys(FOLD_LETTERS).join('') + ']', 'g');

function foldName(s) {
    return String(s == null ? '' : s)
        .toLowerCase()
        .normalize('NFD')
        .replace(FOLD_RE, function (c) {
            return FOLD_LETTERS[c];
        })
        .replace(/[^\0-\x7F]/g, '');
}
