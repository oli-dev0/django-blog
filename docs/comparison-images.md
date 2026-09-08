# Blog Comparison Images

Comparison images are reusable two-sided Blog media records presented as one
article content block. They are separate from ordinary `BlogImage` records
because both sides form one editorial unit and share one caption.

## Editorial behavior

Editors manage comparison records in their own Admin library and select a ready
pair from a comparison content block. A pair contains exactly two independently
processed images, separate alt text for each side, and one shared caption.

Both sides must be ready and storage-complete before the pair can be selected or
published. Failed or incomplete records remain visible to editors but are not
offered as valid content choices.

## Processing and persistence

`BlogImageComparison` stores independent status, dimensions, originals, and
480/800/1200/1600 WebP renditions for each side. Upload validation uses the
shared Blog image byte, pixel, animation, metadata, and format rules.

`process_comparison_image()` processes only the changed side. A failed
replacement restores that side's previous database and file state without
reprocessing or changing the other side. Admin committed-state checks verify
the row and expected files after the surrounding Admin transaction completes.

`BlogImageComparisonBlock` references the reusable pair with `PROTECT`, so a
comparison in article content cannot be deleted accidentally.

## Public rendering

`comparison_sources()` treats storage as authoritative and returns public
sources only when the required files exist. The shared block template renders
one semantic figure: stacked on narrow screens and two columns from 40rem. Both
sides share the caption and use pair-local previous/next navigation in the Blog
image dialog.

Missing files fail closed to the existing unavailable-media presentation.
Comparison records do not create public detail pages, feeds, sitemap entries,
or social metadata.

## Implementation map

- Models and migrations: `apps/blog/models.py`, migrations `0010` and `0018`
- Processing and source checks: `apps/blog/image_services.py`
- Forms and Admin: `apps/blog/forms.py`, `apps/blog/admin.py`
- Rendering: `apps/blog/rendering.py`
- Templates: `blog/blocks/image_comparison.html` and the shared image dialog
- Historical planning material: `docs/blog/features/comparison-images/`

## Tests

`tests/blog/test_images.py`, `test_admin.py`, `test_models.py`, and
`test_views.py` cover processing, replacement isolation, file restoration,
selection, protected deletion, responsive source markup, captions, and dialog
grouping. Import-specific comparison coverage lives in
`tests/blog/test_import_media.py`.

Browser verification is still appropriate for narrow/wide geometry, keyboard
dialog navigation, and swipe behavior.
