# ISBN Normaliser Notes

Provide a standalone normaliser for `{{ISBN|...}}` template usage in wiki text:
- Hyphenate ISBN values in template parameter 1
- Optionally convert ISBN-10 to ISBN-13
- Optionally drop template parameter 2 when it is semantically the same ISBN

Current tool file:
- `isbn_normalise.py`

Range data source:
- `RangeMessage.xml` (International ISBN Agency range message)

## Current behavior
The script targets template syntax:
- `{{ISBN|<param1>}}`
- `{{ISBN|<param1>|<param2>}}`

Rules:
- Always normalise template parameter 1 when possible
- Keep parameter 2 unchanged by default
- Remove parameter 2 only when explicitly enabled and semantically equal

## CLI usage
Format template ISBN values in a text file:

```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format \
  --in-place
```

Format + convert ISBN-10 to ISBN-13:

```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format -to13 \
  --in-place
```

Format + drop equal label (param2):

```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format \
  --drop-equal-label \
  --in-place
```

## Runtime integration (future repo)
Recommended workflow for a bot pipeline:
1. Build candidate page list (or template transclusion index).
2. Fetch page text.
3. Run `isbn_normalise.py` in text mode.
4. Save only when changed.
5. Use clear edit summary (format/to13/drop-equal-label flags).

## Development plan (reminder)
1. Use transclusion index API and process results:
   - `action=query&format=json&maxlag=3&prop=transcludedin&titles=Template%3AISBN&formatversion=2`
   - continue handling (`continue` / `ticontinue`), batching, retry policy.
2. Add optional helper links when fixing in specific cases:
   - Example reminder URL:
   - `https://grp.isbn-international.org/search/piid_solr?keys=978-7-03+%28ISBNPrefix%29`
   - Exact trigger conditions will be specified later.
3. https://www.isbn-international.org/range_file_generation

## Notes
- Validation responsibility can remain with on-wiki template/module.
- This tool focuses on deterministic normalisation and optional migration actions.
