# Article output schema

Write UTF-8 JSON to `outputs/metadata.json` with these fields:

```json
{
  "title": "first normalized title",
  "doi_url": "https://doi.org/10.xxxx/xxxxx",
  "summary": "at most 120 Chinese characters",
  "original_title": "paper title",
  "translated_title": "Chinese translation",
  "corresponding_affiliation": "affiliation",
  "corresponding_country": "country",
  "institution": "short institution name",
  "journal_abbr": "journal abbreviation",
  "total_figure_count": 12,
  "selected_figure_numbers": [1, 3, 4, 7, 11, 12],
  "guide": "one paragraph",
  "figures": [
    {"label": "图1", "description": "one paragraph"}
  ],
  "conclusion": "one paragraph",
  "alternative_titles": ["exactly ten normalized titles"],
  "source_files": ["materials/paper.pdf", "materials/figure.jpg"]
}
```

`figures` must contain exactly six objects ordered by `selected_figure_numbers`. Verify every label against the supplied image filename and the PDF caption.
