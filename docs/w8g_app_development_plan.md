# w8g app development plan

Status: implementation checklist. API schemas and items marked **Confirm** must be agreed before dependent implementation.

## Product scope

- Native iOS app for Hong Kong, built with Swift and SwiftUI in Xcode.
- Users: field workers, students visiting companies or survey sites, tourists and hikers needing access to saved journeys without internet.
- Main tabs: **Explore / Save / AI**. User avatar opens account/settings.
- English and Traditional Chinese; light/dark appearance.
- Charcoal, warm white and soft grey UI matching the supplied icon. Distinct transport colours plus labels.
- Own backend handles routing and optimisation. App sends coordinates.
- Offline scope: inspect saved routes and browse downloaded maps. New route calculation, rerouting, AI and fresh arrivals require login and internet.
- Hiking use means viewing supported saved journeys; trail routing coverage depends on the backend/map data.

## Features and supporting technology

| Feature | Required behaviour | Supporting technology |
|---|---|---|
| Accounts | Register; username/email and password login; recovery; logout; edit profile/password | Supabase Auth + Swift SDK; secure backend adapter for username login |
| Private cloud records | Load personal history at login; persist changes; show retry when sync fails | Supabase Postgres + Row Level Security; stable user IDs |
| Explore | Map-first screen; search bar and microphone; user avatar; floating 1-to-1/multi-stop toggle | SwiftUI; shared map component |
| Place input | Names, coordinates, voice; resolve ambiguous matches before routing | Server place-search adapter using OSM data or Google Places; Apple Speech + microphone permission |
| One-to-one routes | Origin/destination; alternatives; route flow and map; repeated requests where required by API | Own coordinate-based one-to-one endpoint; URLSession + async/await + Codable |
| Multi-stop routes | Up to 10 places; add/edit/remove/reorder; preserve or optimise order | SwiftUI editable stop list; separate own multi-stop endpoint |
| Route display | Geometry, stop markers, transport colours, steps, returned duration; one reusable result screen | Map SDK; shared Route model and SwiftUI route-detail sheet |
| Next arrivals | Choose server GTFS timetable estimate or supplied MTR/bus API; show source, direction, freshness and scheduled/live status | Own arrival adapter; typed arrival models |
| Save | Scrollable route cards; no search bar; route preview and transport flow; one selected alternative per entry | SwiftUI list; local route repository; map preview renderer |
| Offline access | Open Save without login; retain downloaded routes after logout; pan/zoom across downloaded HK coverage; open without routing fetch | SwiftData/local files; offline map SDK and downloaded regional resources |
| AI chat | Place finding, 1-to-1, multi-stop and arrivals; concise text and structured cards; decline unrelated requests | Gemini API through backend; approved tool calls; structured response models |
| AI confirmation | Editable multi-stop confirmation card before any routing request; explicit user confirmation | Shared stop editor; server-side draft validation and confirmation gate |
| Long calculations | Job progress; completion/failure state; notify when ready; tap to open result | Backend job queue/status store; APNs push; iOS UserNotifications |
| Settings | Independent place/arrival source choices; language, appearance, account and offline downloads | SwiftUI settings; persisted preferences; download manager |
| Accessibility | Dynamic Type, VoiceOver, adequate contrast; typed input if speech permission denied | Native SwiftUI accessibility APIs; Xcode accessibility checks |

## AI multi-stop confirmation flow

1. User asks for a multi-stop journey in chat.
2. AI extracts proposed stops and ordered/unordered preference. Place lookup may run to resolve names, but **no routing/optimisation request runs yet**.
3. Show a chat card titled **Confirm your stops**, containing:
   - Numbered place names and addresses; resolved coordinates in the underlying data.
   - Edit/replace, add, remove and drag-to-reorder controls.
   - **Keep this order / Optimise order** selector.
   - Any unresolved/ambiguous place with selectable matches.
   - **Confirm and plan** and **Cancel** buttons.
4. Validate all stops, supported coverage, the stop limit and API-required fields. Disable confirmation until valid.
5. On **Confirm and plan**, submit exactly the confirmed draft revision. Prevent duplicate submission.
6. Replace the action with a pending-job state. Allow the user to leave the chat while processing.
7. Show returned alternatives in a route card. Each alternative opens the shared route screen; save only the selected alternative.

Implementation rules:
- Use the same editable stop component in Explore and AI.
- Editing a resolved place invalidates its old coordinates until the replacement is resolved.
- The AI cannot bypass confirmation using a direct multi-stop tool call.
- Backend validates draft ownership, revision and confirmed state before starting a job.
- Changes after submission create a new draft; never silently modify the running request.
- Clarify missing start/end constraints instead of letting the AI invent them.

## Long-running route jobs and notifications

- Job states: queued → running → succeeded / failed; cancelled only if supported by the backend.
- Job submission returns a job ID quickly. Store owner, confirmed request, status and result reference on the backend.
- Show pending cards in the initiating screen/chat. Display a spinner or status text; percentages only if the server provides progress.
- Use an idempotency key so repeated taps or network retries do not create duplicate jobs.
- Fetch status while foregrounded and reconcile pending jobs when the app resumes or the user logs in.
- If notification permission is granted, backend sends an APNs notification when the result is ready. Tapping it opens that job's result after checking the signed-in account.
- If permission is denied or delivery is missed, results remain accessible in-app. Push is a convenience, not the source of truth.
- Do not rely on an iOS background task or an open phone connection to finish routing.
- Keep push payloads minimal: job identifier and generic message; fetch private route details after authentication.
- Remove/reassociate device-token bindings on logout/account changes to avoid another account's notifications.

## App structure and shared models

Suggested structure:

```text
App/                 app entry, dependency setup, navigation
Features/            Auth, Explore, Save, AI, Settings, RouteDetail
Components/          StopEditor, StopConfirmationCard, RouteCard, MapView
Models/              Place, RouteDraft, Route, RouteAlternative, Arrival, RouteJob
Services/            Auth, Places, Routing, Arrivals, AI, Notifications
Storage/             CloudHistory, LocalRoutes, OfflineMaps, Preferences
```

- SwiftUI views + feature state models + service protocols; inject mock services during development.
- RouteDraft: ID/revision, resolved stops, ordering mode, start/end constraints, selected sources, confirmation state.
- Route: stop sequence, geometry, legs, instructions, transport labels, duration if supplied, source and calculation timestamp.
- SavedRoute: exactly one route snapshot, local ID, cloud owner/reference where relevant, offline-map dependency and download status.
- AI message: text plus typed payloads for place choices, stop confirmation, pending jobs and route alternatives.
- Shared map screen accepts an existing route object; opening Save or AI results must not trigger a fresh routing call.

## Storage, maps and source settings

- Supabase is authoritative for account records/history; the phone must still persist offline routes and map resources.
- Queue/retry cloud changes on reconnection; do not claim sync succeeded before the server confirms it.
- Local guest mode exposes downloaded routes only, not private cloud history. Explain retained local access at save time.
- Download a shared HK map package rather than duplicate map data per route.
- Include geometry/tiles, styles, fonts and symbols needed for offline rendering; validate before marking a route offline-ready.
- Show package size, download progress, supported coverage/zooms, missing regions and deletion controls.
- **Candidate:** MapLibre Native; final SDK and permitted offline dataset require an early device prototype.
- Google Places is a place-search option, not a Google routing switch. Validate its display/storage conditions against the chosen map renderer.
- Keep required map/data attribution. Do not use standard public OSM tile servers for bulk offline downloads.
- Settings changes apply to new requests; saved snapshots retain their source metadata.
- GTFS timetable estimates remain labelled scheduled; offline cached arrival values are not fresh live arrivals.

## Backend contracts to agree before integration

| Contract | Minimum app-facing requirements |
|---|---|
| Place search | Query/language/source; candidate ID, name, address, coordinates, coverage/errors |
| One-to-one routing | Origin/destination coordinates; route alternatives with geometry/legs; repeated-call semantics |
| Multi-stop routing | Confirmed stop coordinates, ordering mode, start/end constraints, draft revision and idempotency key; job ID |
| Job status/result | Account-owned job ID, state, timestamps, error/retry information, result alternatives |
| Arrival lookup | Stop/station, service and direction IDs, provider choice; estimate, scheduled/live status, source timestamp |
| AI chat/tools | Conversation context; allowed intents; typed confirmation draft; no multi-stop submission without confirmed draft |
| Notifications | Authenticated device registration/removal; completion event; APNs delivery; job-result deep link |
| Offline maps | Package/version, coverage, zoom range, size, resource URLs and integrity checks |

## Build order

1. **Foundation:** confirm API contracts, deployment target, map SDK/offline feasibility and Gemini model; create Xcode project, theme, tabs and shared models.
2. **Accounts and input:** Supabase login/privacy, profile/settings, both place providers, coordinates and speech.
3. **Direct routing:** one-to-one and multi-stop forms, shared stop editor, route alternatives and map results.
4. **Save and offline:** local route snapshots, HK map downloads, guest Save mode, cloud history synchronisation.
5. **Arrivals and jobs:** both arrival sources, job status/recovery, APNs completion notifications.
6. **AI:** constrained tools, editable multi-stop confirmation, alternative cards and shared map navigation.
7. **Integration and release preparation:** bilingual review, accessibility, failure handling, device tests and demonstration rehearsal.

Keep the previously planned 16–22 November buffer before the earliest presentation on 23 November 2026.

## Acceptance checklist

- [ ] AI multi-stop draft makes zero routing calls until explicit confirmation.
- [ ] Edits, ordering mode and coordinates match the submitted request exactly.
- [ ] Ambiguous places block submission; double taps/retries create one job.
- [ ] Result becomes accessible after leaving the screen, app suspension and a later login.
- [ ] Notification tap opens the correct account-owned job; denied permission still works in-app.
- [ ] One-to-one, repeated-call and ten-stop flows match backend contracts.
- [ ] AI alternative selection opens and saves the chosen route only.
- [ ] Saved routes open without a new routing request.
- [ ] After download, restart/logout/airplane mode still allow map panning and route inspection.
- [ ] Two accounts cannot read each other's cloud records or receive misdirected route notifications.
- [ ] Both source settings affect direct and AI flows; scheduled/live labels remain correct.
- [ ] English/Traditional Chinese, text/voice input and permission-denied states work.

## Decisions still to confirm

- Minimum iOS version and final offline map SDK/provider/package coverage.
- Final Gemini model and backend hosting/job-queue choice.
- Whether unordered optimisation fixes an origin, a destination, both, or neither; round-trip support if any.
- Final routing/job schemas, request limits, timeout/retry and cancellation support.
- Whether cloud-synced saved routes auto-download on a new device or require an explicit download.
- Notification scope beyond route completion, such as failure notifications.

## Saved-route sharing, availability and waypoint photos

### Text share code and import

- Add **Share route → Copy code** and **Save → Import code**. Use the iOS share sheet for user-directed text sharing; import only after an explicit paste action.
- Proposed format: `W8G1:<base64url payload>`. Versioned Codable JSON includes one selected route, instructions/geometry, schedule snapshot, waypoint annotations, permitted photo bytes and attribution. Compression and exact limits are implementation decisions.
- Offer route-only or route-with-photos export. Embed permitted compressed JPEG/HEIC photo bytes as Base64; show final text size before copying. Base64 is encoding, not encryption, and adds about 33% to binary size.
- Do not embed map packages, credentials, account IDs, private chat/history or device tokens. Recipients need their own compatible downloaded map for offline map browsing.
- Preview included locations/photos and availability warnings before sharing. The code itself reveals the route to anyone who receives it.
- Import validates version, bounded encoded/decoded size, image count/dimensions, coordinates, schema and integrity; reject corrupt/truncated/unsupported input. Do not execute payloads or automatically fetch embedded URLs.
- Show an import preview, missing-map/photo status and service dates before confirming a new local Save entry. Import must not automatically run routing or overwrite another route.
- Permit local import/export without login; cloud sync requires the signed-in account. Imported route information is an unverified snapshot; checksum detects corruption, not trustworthiness.
- For oversized codes, ask the user to reduce photo resolution/count or export route-only; do not silently discard images. Final size cap requires testing across messaging apps.
- Tech: Codable, Foundation Base64, optional compression, image encoding, SwiftUI ShareLink/UIActivityViewController and explicit paste control; local route repository.

### Transit availability reminders

- Saved cards/details and import previews show **Planned departure**, **Service dates/days**, applicable time window and **Last checked**, in Asia/Hong_Kong time.
- Backend returns per-leg operating constraints and, when supported, a feasible journey departure window that accounts for transfers. Do not infer whole-route validity by merely overlapping service hours.
- Preserve GTFS service-day semantics, including overnight times beyond 24:00, weekday/weekend patterns and date exceptions.
- Warn on save/open/import when outside a known window, after the schedule validity date, or when data is stale/unknown. State that fresh service operation is unverified offline.
- Do not promise a usable route from a saved ETA or first/last train time alone. Provide **Refresh availability** when logged in and online; no silent route recalculation.
- These are in-app reminders. Optional scheduled local notifications are separate from route-job completion push and need a user-selected departure time.
- Tech: route API/GTFS service-calendar metadata, typed per-leg constraints, date/time handling and local evaluation; UserNotifications only if optional departure reminders are enabled.

### Midpoint annotations and reference photos

- During saving, let users add named reference waypoints on the route/map: coordinate, note, route position, viewing direction and optional photo. They help hikers/visitors recognise landmarks offline; images do not determine the user's position.
- Distinguish **reference waypoint** from **routing stop**. Reference points do not alter the saved geometry. A new required routing stop creates an edited route draft and requires online recalculation before saving.
- At preparation time, query an imagery-provider adapter for the selected point: CSDI dataset or Google Street View/other suitable Google imagery API. Check actual ground-level imagery coverage; aerial imagery is not a street-level photo substitute.
- Present available images with source, capture date where known, location and viewing direction. User selects useful images and notes; show a clear no-coverage result rather than inventing a view.
- Store/download and embed in share text only when the specific source permits offline retention and redistribution. Track those permissions separately per image with attribution/licence metadata.
- Google Street View Static API generally prohibits image prefetching/storage except specified exceptions; use online previews unless applicable permission is established. Do not Base64-export restricted Google images.
- CSDI is a discovery portal, not a guarantee of hiking street-view coverage or uniform image licensing. Select a suitable dataset and verify its terms before enabling offline downloads/export. The provider choice remains unresolved.
- Offline waypoint gallery shows downloaded permitted photos and notes; online-only imagery is clearly labelled. Photos are optional; saving the route can proceed when a photo is unavailable.
- Extend models: ReferenceWaypoint, PhotoAsset (local bytes/reference, MIME type, dimensions, provider/dataset, capture date, heading, attribution, offline/export permission), RouteServiceValidity.
- Tech: SwiftUI map annotations/gallery, provider adapter + URLSession, local files/image decoder, Codable share serializer and permission-aware export.

### Integration and tests for these additions

- Implement route-only import/export and availability metadata with Save; add imagery after dataset/permission feasibility is verified. Keep the final presentation buffer.
- Test round-trip text sharing with and without permitted photos, oversized/truncated codes, invalid image data and missing offline maps.
- Test import while logged out, duplicate import, account switch and excluded private metadata.
- Test weekday/holiday service, midnight rollover, expired schedules, unknown windows and transfer feasibility.
- Test waypoint editing without rerouting, routing-stop changes with recalculation, absent imagery, online-only images and offline photo viewing.
- Confirm source attribution survives sharing and restricted imagery never enters offline/export payloads.

Sources checked 25 September 2026:
- Google Street View Static API policies: https://developers.google.com/maps/documentation/streetview/policies
- CSDI terms and conditions: https://static.csdi.gov.hk/csdi-webpage/doc/TNC
- CSDI dataset information: https://www.csdi.gov.hk/about-us/csdi-information.html
