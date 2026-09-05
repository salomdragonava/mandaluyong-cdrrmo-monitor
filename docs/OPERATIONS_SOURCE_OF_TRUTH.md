# Mandaluyong Flood Monitor — Operations Source of Truth

Last updated: 2026-09-05

## Purpose

This document is the operational source of truth for the Mandaluyong flood-monitor bot. It records the lessons learned from the August 27–September 5 monitoring cycle, the production logic boundaries, the implementation strategy, and the rules for future changes.

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

The useful working model is:

`prior accumulated system load + renewed rainfall forcing → threshold crossing → flooding`

The monitoring data supports investigating persistence/accumulation before the flood and stronger forcing during the flood, but it does not yet prove physical stored-water accumulation. Do not change production thresholds from this hypothesis alone.

### 1.3 A low classification is not the same as low flood risk
A source can classify conditions as low while the local system is still carrying unresolved state. Conversely, a high PAGASA warning does not prove local flooding. The bot must preserve these distinctions instead of collapsing them into one score.

### 1.4 Radar failures taught us to distinguish availability from freshness
The previous lightweight radar collector depended too heavily on catching one Playwright network response. A valid radar frame can exist even when the response event is missed. A known previous frame can also remain valid while the current source is stale or unreachable.

Therefore radar state must distinguish:
- `ok`: a new usable frame was obtained
- `unchanged_frame`: a frame was obtained/known but has not advanced
- `stale_or_unusable_source`: the radar application exposed URLs, but no newer usable image was recovered
- `source_unreachable`: no usable radar URL/source could be discovered
- `insufficient_frames`: current frame exists but there is not enough history for movement tracking

A stale frame is evidence about freshness, not proof that the PAGASA source is down.

### 1.5 Observability must survive failure
Every collector should preserve enough evidence to answer "what failed?" without rerunning blindly. For radar this means discovered URLs, HTTP status, content type, byte count, image signature/validity, TLS fallback, and exception reason.

### 1.6 Do not hide collection failures behind successful workflows
A GitHub Actions job can complete successfully while the data collector inside it fails. Workflow success therefore means the pipeline executed, not that every source produced fresh data. The bot must expose source state explicitly in the notification and persist diagnostics.

## 2. Implementation strategy

### Collection pattern
1. Collect official PAGASA warning state.
2. Collect local/manual flood-monitor state separately.
3. Collect radar independently and defensively.
4. Run radar threat classification only when radar data is fresh enough to qualify.
5. Run the rolling 3-day assessment using historical state.
6. Build one unified Telegram status message.
7. Send the unified status once per scheduled run; send escalation only when the escalation file is intentionally produced.
8. Persist state to the repository so the next run has an auditable baseline.

### Radar recovery pattern
Use a layered approach, in this order:
1. Capture a qualifying `mosaic-hybrid` response directly when Playwright sees it.
2. Inspect browser performance resources for the actual radar URL if the response event was missed.
3. Inspect relevant radar/map JavaScript for explicit mosaic URLs when necessary.
4. Attempt bounded direct retrieval of discovered URLs, newest timestamp first.
5. Retry with targeted TLS fallback only when the normal request fails with a TLS/OS-level error.
6. If recovery fails, retain the previous known frame separately and report the source condition accurately.

Do not replace a robust collector with a shorter collector merely because the shorter collector is easier to run. Prefer bounded recovery paths with explicit failure states.

## 3. Logic boundaries

### Action status
- RED/high-risk/imminent → LEAVE
- ORANGE/escape elevated/high/radar candidates → PREPARE TO LEAVE
- YELLOW/watch/low-moderate/elevated escape → PREPARE
- validated precursor signal → PREPARE
- otherwise → SAFE

These are operational communication states, not a claim of physical flood probability.

### Radar threat logic
Radar is diagnostic/supporting evidence. It does not independently create an `imminent` flood prediction. Radar candidates must satisfy the configured score, distance, pixel, and precipitation-class requirements and must come from a qualifying fresh radar state.

### Assessment logic
The assessment combines recent state and validated precursor logic. A rising numerical risk score alone does not trigger an imminent prediction. The validated precursor concept is accumulation/persistence first, followed by renewed rainfall or warning forcing.

## 4. Workflow architecture

There should be one production scheduled workflow for the bot: `.github/workflows/pagasa.yml`.

It owns the complete hourly sequence and state commit. Manual/debug workflows that duplicate production collection create race conditions, duplicate notifications, stale caches, and handover ambiguity. They should be removed or kept outside the production repository only when there is a concrete diagnostic need.

The production workflow should avoid unnecessary GitHub Actions caches when state is already committed to `main`. Repository state is the durable baseline; the workflow should read it, update it, and commit the resulting state once.

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
- Check whether the change creates duplicate or contradictory alerts.
- Run the smallest relevant verification before deployment.
- Record the reason and expected behavior in GitHub issues.

After deployment:
- Verify the next scheduled runtime result.
- Do not close an issue solely because code was committed when runtime behavior is an acceptance criterion.
- Close completed implementation work and leave only concrete runtime verification or follow-up investigation open.

## 7. Current outstanding work

### Issue #1 — Multi-day water accumulation precursor
Still open. This is an analytical validation task. It must compare flood and non-flood days, quantify persistence/recovery, handle missing data explicitly, and identify whether Aug 28 materially changes the starting condition for Aug 29. No production threshold change until validated.

### Issue #2 — PAGASA radar unavailability
Implementation is substantially completed: robust URL discovery, direct-download fallback, TLS fallback, and distinct stale/unreachable states are now in production code. Runtime verification remains outstanding. Close only after a scheduled run demonstrates the new state/recovery behavior and the workflow no longer silently masks radar collection failure.

## 8. Handover rule

A future maintainer should be able to answer these five questions from the repository without reconstructing the history:

1. What is official source data versus local/manual data?
2. What does each status mean?
3. What conditions trigger PREPARE/LEAVE?
4. What happens when a source is stale or missing?
5. Which assumptions are validated facts versus hypotheses under investigation?

If a change makes any of these answers harder to determine, the change should be simplified or documented before it is considered complete.
