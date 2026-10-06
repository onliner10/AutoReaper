# Faust manual (offline)

Pages from the Faust documentation, [grame-cncm/faustdoc](https://github.com/grame-cncm/faustdoc)
`src/manual` at commit 875ebbce, unchanged. They are CC0 (see LICENSE); the site is https://faustdoc.grame.fr.

| File | Contents |
|---|---|
| syntax.md | The language: definitions, composition operators, `with`/`letrec`, iterations, UI elements and their metadata, foreign functions |
| midi.md | MIDI metadata for controls (`[midi:key 60]`, `ctrl`, `keyon`, ...), polyphony |
| errors.md | Compiler error messages and what causes them |

The standard library (`ba.`, `fi.`, `an.`, ...) is documented in its own `.lib` files, which come with
Faust; see the Faust section of ../plugins.md for where they are and how to search them.

To refresh: copy `src/manual/{syntax,midi,errors}.md` from a newer faustdoc checkout and update the commit above.
