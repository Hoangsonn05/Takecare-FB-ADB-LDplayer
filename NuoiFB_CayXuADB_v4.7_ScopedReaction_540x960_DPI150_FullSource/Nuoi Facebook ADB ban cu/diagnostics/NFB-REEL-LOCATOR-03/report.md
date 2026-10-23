# NFB-REEL-LOCATOR-03 — read-only diagnostic

TASK RESULT: BLOCKED — NEEDS EVIDENCE (full-screen Reel viewer screenshot at the reported failure)
HYPOTHESIS: PARTIAL

## Root cause and first divergence
Actual wrong Reel/Like selection was not reproduced on available images. Source confirms a plausible gap: the Nuôi FB runner calls `TemplateMatcher.find_reel_like_button` on its captured frame, then uses the first candidate scoring at least 0.55. This path neither checks a current Reel viewer state nor validates a vertical Like/Comment/Share action rail. The first potential divergence is `find_reel_like_button_image` returning that candidate after template scoring. `run_reel_loop` trusts the tab entered earlier and does not revalidate the viewer before each reaction. The separate Auto cày xu `ReelLikeLocator` has vertical action rail validation, but Nuôi FB does not call it.

## ROI, template and candidate policy
- Full 540x960 frame: ROI (313,28,540,945), covering the right 42% of nearly the full height; this includes Feed Reel previews.
- Narrow rail crop 50x208 or 56x244: the entire crop is the ROI.
- Configured template `nutlikereel.png` resolves to existing `nutlikereel.png.jpg`; raw size 48x54, Canny-prepared icon 26x24 after removing the dynamic counter. Scales: 0.65, 0.75, 0.85, 0.95, 1.00, 1.05, 1.10, 1.20, 1.30, 1.40 at configured 150 DPI.
- Canny edge matching uses TM_CCOEFF_NORMED. Candidates are ranked by score, deduplicated spatially, and the first at or above 0.55 is returned. The extraction floor is 0.23 at this threshold and is not the acceptance threshold.

## Reproduction on available images
| Image / visual class | ROI | Best and selected candidate | Outcome |
|---|---|---|---|
| `reel_action_rail_50x208.png` / actual Reel rail crop | (0,0,50,208) | (23,21), 0.584 | Accepted, visually Like; Comment and Share below |
| `domcamxucreel.png` / partial Reel with reaction panel | (204,10,352,348) | (315,92), 0.738 | Accepted, visually Like |
| `camxucreel.jpg` / older Reel rail crop | (0,0,56,244) | (30,37), 0.614 | Accepted, visually Like |
| 9 saved LD17 screenshots / Feed | (313,28,540,945) | Best range 0.382–0.415; none selected | No false positive observed |
| Current `emulator-5588` screenshot / Feed with horizontal action row and Reel previews | (313,28,540,945) | Best (394,575), 0.382; none selected | No false positive observed |

On the current Feed image, lower-scored candidates lie within Reel preview artwork, proving the broad ROI reaches that area. They remain below the 0.55 acceptance threshold. No observed false negative on the three reference crops. A full-screen Reel viewer frame and failure timestamp are absent, so full-screen false negatives and the reported wrong selection cannot be assessed. No XML snapshot is saved, so UI node structure cannot be independently checked for the failing frame.

The supplied `configs/default.json` has `run_reel=false` and `reel_reaction_enabled=false`; it does not establish the user's runtime settings.

## Evidence and boundaries
- `locator_probe.txt`: every probed image, ROI, best score, selected candidate, and top five candidates.
- `*_candidates.png`: annotated ROI and candidate locations for current Feed and three Reel references.
- `saved_screenshots_contact_sheet.png`: all saved LD17 screenshots, each visually Feed.
- `current_emulator-5588.png`: read-only capture from the specified ADB serial. No tap, swipe, reaction, app launch, or configuration change was performed.
- `automation/jobs/detectors/reel_like.py` contains a separate structural validator; Auto cày xu source was only read.

## Recommended write scope for a future patch
After a failing full-screen Reel screenshot and matching XML/log are supplied, constrain a future patch to `automation/feed_runner.py` and, only if the candidate policy itself is proven wrong, `core/template_matcher.py`, plus focused tests. Verify the current screen state and Like/Comment/Share rail before accepting a candidate. Do not change the threshold or hardcode a coordinate based on these crops.

## Targeted test plan
1. Replay the failing 540x960 frame and XML through the current locator. Record selected candidate, all scores above the extraction floor, ROI, and ground-truth Like/Comment/Share positions.
2. Replay an actual Reel viewer frame (positive), a Feed frame containing Reel previews (negative), and a Reel rail without Like (negative).
3. Assert the intended candidate is accepted only when both screen identity and action rail agree. Preserve Safe Swipe, serial binding, and Auto cày xu behavior.
