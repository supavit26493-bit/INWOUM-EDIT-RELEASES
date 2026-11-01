# Super style — user standard, 25 October 2026

Font source: user-supplied Kanit.zip, not a downloaded replacement.
Family: Kanit. Style: Bold. PostScript name: Kanit-Bold.
Kanit-Bold.ttf SHA-256: 2dc2caa0815dd6fde3560d115b1fdda20adcd8033c8bebed5f4b732f4e84302d
Text: one line, dark yellow, no shadow.
User reference screenshot: font size 100; paragraph alignment horizontal center and vertical center.
Text geometry must be centered locally, with downward placement applied separately using Motion.
Do not offset the text's local origin downward to simulate Motion.
Native Premiere template: assets/super-style.xml, derived from Desktop/supersetting.xml supplied by the user.
Preserve its native GraphicAndType Source Text style tables, local center, Basic Motion and Anchor Point.
Basic Motion: center 0 / 0.330556, anchor offset 0 / 0; at 1920x1080 Premiere displays Position 960 / 897 and Anchor Point 960 / 540.
Use native font size 100. At other image widths scale the graphic uniformly by width / 1920; compensate its local center offset at that scale. Basic Motion remains separate.
Replace only the text string and timeline timing; keep one line and no added effects.
Keep the invisible one-frame frame-zero anchor in text-only export; never count it as a Super item.
Verification 25 October 2026: native XML imported in Premiere Pro 2024, Thai text correctly changed, no missing-font prompt; user viewed the result and approved continuing.
Animation presets are user-added and were not comprehensively tested.
