# Relayna brand assets

Selected, unmodified exports from the owner-supplied `@relayna/design-system`
0.4.0 package, supplied on 2026-10-26. Identity: Natural Connection / Mulberry &
Linen; Slim Leaf symbol; Open Signal campaign. The interface, documentation and
refreshed outlined product lockups use official **Manrope 4.505**, variable
weights 200–800. The approved Studio tagline remains “Make distributed work
visible.”

`apps/studio/src/typography.css` imports the unmodified `css/fonts.css`;
`studio.css` uses the shared sans and code-role tokens. Both roles use Manrope
as specified by this design-system version. Code preserves whitespace; tables
use semantic columns and tabular numerals. Use normal tracking and
`font-synthesis: none`: upstream supplies no italic face. `font-display: swap`
and system fallbacks keep loading text and unsupported scripts readable.

`manifest.json` records SHA-256 hashes. `provenance/manrope.json` records the
pinned font revision, hashes and lossless WOFF2 conversion evidence. Refresh
from authoritative exports rather than editing generated CSS or artwork.
Consumer mappings belong outside this directory. The Studio favicon is copied
to `apps/studio/public/favicon.ico`.

Manrope is licensed under SIL Open Font License 1.1. Keep `fonts/OFL.txt` with
its font files; the same license is copied to `apps/studio/public/fonts/OFL.txt`
so production bundles include it. This license is independent of the private
Relayna artwork/package policy. See [usage and provenance](USAGE-RIGHTS.md).
