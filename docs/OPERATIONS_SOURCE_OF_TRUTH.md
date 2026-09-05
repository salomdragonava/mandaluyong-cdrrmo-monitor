# Mandaluyong Flood Monitor — Operations Source of Truth

Last updated: 2026-09-05

## Purpose

This document is the operational source of truth for the Mandaluyong flood-monitor bot. It records the lessons learned from the August 27–September 5 monitoring cycle, production logic boundaries, implementation strategy, and rules for future changes.

## 1. What we learned

### 1.1 Separate source truth from interpretation
- PAGASA warning level is an official external classification; it is not a local flood observation.
- ProjectLIGTAS `risk_level` is treated as a source classification.
- ProjectLIGTAS numerical `risk_score` is used for trend detection only.
- Local monitoring-point values are proxies and must not be treated as direct measurements of stored water.
- Missing data is not `NORMAL` and must never be silently converted to zero.

### 1.2 The Aug 28 → Aug 29 pattern is a hypothesis, not yet a production rule
Observed ground truth:
- Aug 27: no flood
- Aug 28: no flood
- Aug 29: confirmed flood, approximately 1 PM–8/9 PM
- Aug 30–Sep 1: no confirmed flood

Working model:

`prior accumulated system load + renewed rainfall forcing → threshold crossing → flooding`

The data supports investigating persistence/accumulation before the flood and stronger forcing during the flood, but does not yet prove physical stored-water accumulation. Do not change production thresholds from this hypothesis alone.

### 1.3 A low classification is not the same as low flood risk
A source can classify conditions as low while the local system is still carrying unresolved state. Conversely, a high PAGASA warning does not prove local flooding. Preserve these distinctions instead of collapsing them into one score.

### 1.4 Radar failures taught us to identify the real source architecture
The PAGASA radar page is a shell that embeds the PANaHON map application. The live radar products are exposed by PANaHON, not necessarily by the historical `/radar/timeline/mosaic-hybrid/` URL pattern the bot originally searched for.

The failed 2026-09-05 runtime proved this: the hardened collector still returned `source_unreachable`, with zero discovered URLs and zero download diagnostics, while the live PAGASA radar page itself was available and its iframe exposed Radar Mosaic / Hybrid Reflectivity / Rain Rate. The failure was therefore primarily an endpoint-discovery assumption, not proof that PAGASA radar was down.

The current recovery strategy is API-first:
- PANaHON radar timeline endpoint for `mosaic-qpe` is the primary collection path.
- Timeline `image_urls` and observation timestamps are used to select the newest frame.
- Direct image retrieval is validated by PNG/JPEG signature.
- The old browser `mosaic-hybrid` discovery remains a fallback only.
- Source discovery, timeline retrieval, image retrieval, and image freshness are recorded independently.

### 1.5 Availability, freshness, and workflow success are different dimensions
A workflow can succeed while a collector fails. A source can be reachable while exposing only an old frame. A frame can be successfully downloaded but still be unsuitable for movement tracking if there is no prior frame.

Therefore the radar state machine distinguishes:
- `ok`: a new usable frame was obtained
- `unchanged_frame`: a valid frame timestamp has not advanced
- `stale_or_unusable_source`: source data was discovered but no newer usable image was recovered
- `source_unreachable`: no usable radar source could be discovered
- `insufficient_frames`: a current frame exists but there is not enough history for movement tracking

### 1.6 Observability must survive failure
Every collector should preserve enough evidence to answer "what failed?" without rerunning blindly. For radar this includes source endpoint, frame count, discovered URLs, HTTP status where available, content type, byte count, image signature/validity, fallback method, and exception reason.

## 2. Implementation strategy

### Collection pattern
1. Collect official PAGASA warning state.
2. Collect local/manual flood-monitor state separately.
3. Collect radar independently and defensively.
4. Run radar threat classification only when radar data is fresh enough to qualify.
5. Run the rolling 3-day assessment using historical state.
6. Build one unified Telegram status message.
7. Send the unified status once per scheduled run; send escalation only when intentionally produced.
8. Persist state to the repository so the next run has an auditable baseline.

### Radar recovery pattern
Use a layered approach with the most authoritative machine-readable source first:
1. Call the PANaHON radar timeline API for the required product.
2. Select the newest timestamp and associated image URL.
3. Download and validate the image.
4. Compare the timestamp with the previous frame.
5. Only if the API path fails, fall back to browser discovery of radar URLs.
6. If recovery fails, retain the previous known frame separately and report source condition accurately.

Do not start with UI scraping when the embedded application exposes a machine-readable data interface. Do not infer endpoint structure from an old implementation when the live application exposes a different API.

### General recovery principle
When a collector fails repeatedly with an empty discovery set, stop adding more retries to the same discovery mechanism. Re-investigate the source architecture and identify whether the application has changed its transport/API boundary.

## 3. Logic boundaries

### Action status
- RED/high-risk/imminent → LEAVE
- ORANGE/escape elevated/high/radar candidates → PREPARE TO LEAVE
- YELLOW/watch/low-moderate/elevated escape → PREPARE
- validated precursor signal → PREPARE
- otherwise → SAFE

These are operational communication states, not claims of physical flood probability.

### Radar threat logic
Radar is diagnostic/supporting evidence. It does not independently create an `imminent` flood prediction. Radar candidates must satisfy configured score, distance, pixel, and precipitation-class requirements and must come from a qualifying fresh radar state.

### Assessment logic
The assessment combines recent state and validated precursor logic. A rising numerical risk score alone does not trigger an imminent prediction. The validated precursor concept is accumulation/persistence first, followed by renewed rainfall or warning forcing.

## 4. Workflow architecture

There should be one production scheduled workflow for the bot: `.github/workflows/pagasa.yml`.

It owns the complete hourly sequence and state commit. Duplicate scheduled workflows create race conditions, duplicate notifications, stale caches, and handover ambiguity.

Repository state is the durable baseline. Avoid unnecessary Actions caches when state is already committed to `main`.

## 5. Source-of-truth hierarchy

1. Official PAGASA source data for PAGASA warning status.
2. Confirmed local monitoring observations for actual local flood status.
3. Persisted bot telemetry for historical sequence analysis.
4. Radar imagery/derived movement as supporting evidence.
5. Model/heuristic scores as interpretation, never as a replacement for source observations.

When sources conflict, preserve the conflict in the alert and logs rather than silently choosing one.

## 6. Change-management rules

Before changing production logic:
- Identify the source field being changed.
- State whether the change affects collection, classification, trend detection, or action status.
- Check confirmed flood/non-flood ground truth.
- Check missing-data behavior.
- Check duplicate or contradictory alerts.
- Run the smallest relevant verification before deployment.
- Record the reason and expected behavior in GitHub issues.

After deployment:
- Verify the next scheduled runtime result.
- Do not close an issue solely because code was committed when runtime behavior is an acceptance criterion.
- If a runtime failure contradicts the implementation assumption, reopen the implementation investigation rather than treating the failed runtime as an unrelated verification task.

## 7. Current outstanding work

### Issue #1 — Multi-day water accumulation precursor
Still open. Analytical validation only. Compare flood and non-flood days, quantify persistence/recovery, handle missing data explicitly, and identify whether Aug 28 materially changes the starting condition for Aug 29. No production threshold change until validated.

### Issue #3 — Verify and stabilize live radar collection
Active. The previous implementation was not sufficient because it searched the wrong/obsolete radar transport path. The next verification must prove the PANaHON API-first path captures a current radar frame in the scheduled environment and that movement tracking receives two sequential usable frames.

Issue #2 is closed only as the original implementation milestone; it must not be interpreted as proof that live radar collection was solved.

## 8. Handover rule

A future maintainer should be able to answer these five questions from the repository without reconstructing the history:

1. What is official source data versus local/manual data?
2. What does each status mean?
3. What conditions trigger PREPARE/LEAVE?
4. What happens when a source is stale or missing?
5. Which assumptions are validated facts versus hypotheses under investigation?

If a change makes any of these answers harder to determine, simplify or document it before considering it complete.
