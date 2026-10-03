import sys, time, json, secrets, threading
from pathlib import Path
from datetime import datetime, timedelta
from . import storage, cloud

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "route_checker"))
import importlib.util

_spec = importlib.util.spec_from_file_location(
	"w8g_routing_engine", ROOT / "route_checker/server.py"
)
router = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(router)
import optimizer

manager = None
MODES = ["mtr", "bus", "ferry", "tram", "light_rail", "funicular"]


def init(port):
	global manager
	router.OTP = f"http://127.0.0.1:{port}/otp/gtfs/v1"
	router.PLACES = json.loads((ROOT / "route_checker/places.json").read_text())["places"]
	manager = optimizer.Manager(router)


def validate(d):
	if not isinstance(d.get("includeAlternatives", True), bool):
		raise ValueError("includeAlternatives must be true or false.")
	maximum = d.get("maxResults", 6)
	if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 10:
		raise ValueError("maxResults must be an integer from 1 to 10.")
	if d.get("channel", "explore") not in ("explore", "ai"):
		raise ValueError("Invalid request channel.")
	stops = d.get("stops")
	if not isinstance(stops, list) or not 2 <= len(stops) <= 10:
		raise ValueError("Choose 2–10 resolved places.")
	if d.get("order") not in ("ordered", "optimise"):
		raise ValueError("Choose a stop ordering mode.")
	stop_stays(d)
	for s in stops:
		router.validate_request(
			dict(
				origin=s,
				destination=s,
				modes=d.get("modes", MODES),
				preference=d.get("preference", "fastest"),
				departure=d.get("departure"),
			)
		)
		if not isinstance(s.get("name"), str) or len(s["name"]) > 300:
			raise ValueError("Invalid place name.")
	return {
		k: d[k] for k in ("stops", "order", "departure", "modes", "preference", "channel", "includeAlternatives", "maxResults", "defaultStayMinutes", "stopStayMinutes") if k in d
	}


def normalize(it, stops, departure):
	return dict(
		id=secrets.token_hex(12),
		stops=stops,
		legs=it["legs"],
		trips=it.get("trips"),
		stopStayMinutes=it.get("stopStayMinutes"),
		duration=it.get("displayDurationSeconds", it.get("duration", 0)),
		start=it.get("start", departure),
		end=it.get("end"),
		transfers=it.get("transfers", 0),
		walkDistance=it.get("walkDistance", 0),
		calculatedAt=datetime.now(router.HK).isoformat(),
		source="Local OTP · experimental HK GTFS",
		departure=departure,
		availability={
			"date": departure[:10],
			"text": ("Planned for this departure only. Transit operates on specific days and hours. Check again before travel; offline times are a saved timetable snapshot."
				+ (" Warning: the map has unverified endpoint connections; the displayed time excludes those connections." if it.get("hasUnverifiedAccess") else "")),
			"verifiedServiceWindow": False,
		},
		feedWindow=[x.isoformat() for x in router.service_window()],
		requestedDeparture=it.get("requestedDeparture", departure),
		originWaitSeconds=it.get("originWaitSeconds", 0),
		displayDurationSeconds=it.get("displayDurationSeconds", it.get("duration", 0)),
		accessGaps=it.get("accessGaps", []),
		hasUnverifiedAccess=it.get("hasUnverifiedAccess", False),
		planningWarnings=it.get("planningWarnings", []),
	)


def trip_summary(itinerary, leg_start, departure):
	"""Keep visit boundaries without duplicating all geometry in saved/share data."""
	return dict(
		legStartIndex=leg_start,
		legEndIndex=leg_start + len(itinerary["legs"]),
		duration=itinerary.get("displayDurationSeconds", itinerary.get("duration", 0)),
		start=itinerary.get("start", departure),
		end=itinerary.get("end"),
		departure=departure,
		transfers=itinerary.get("transfers", 0),
		walkDistance=itinerary.get("walkDistance", 0),
	)


def progress(j, msg):
	with storage.db() as c:
		c.execute("UPDATE jobs SET progress=? WHERE id=?", (msg, j))


def run(j, d):
	try:
		with storage.db() as c:
			c.execute("UPDATE jobs SET state='running' WHERE id=?", (j,))
		stops = d["stops"]
		stays = stop_stays(d)
		departure = d["departure"]
		modes = d.get("modes", MODES)
		pref = d.get("preference", "fastest")
		if len(stops) == 2 or d["order"] == "ordered":
			legs = []
			trips = []
			total = 0
			opts = []
			cursor = departure
			gaps = []
			warnings = []
			for i in range(len(stops) - 1):
				cursor = (datetime.fromisoformat(cursor) + timedelta(minutes=stays[i])).isoformat()
				progress(j, f"Planning journey {i + 1} of {len(stops) - 1}")
				result = router.plan(
					dict(
						origin=stops[i],
						destination=stops[i + 1],
						modes=modes,
						preference=pref,
						departure=cursor,
						includeAlternatives=d.get("includeAlternatives", True) if len(stops) == 2 else False,
						maxResults=d.get("maxResults", 6) if len(stops) == 2 else 1,
					)
				)
				its = result["itineraries"]
				for item in its:
					item["planningWarnings"] = result.get("warnings", [])
				if not its:
					raise ValueError(
						"No available route for "
						+ stops[i]["name"]
						+ " → "
						+ stops[i + 1]["name"]
						+ ". Try another time or transport selection."
					)
				if len(stops) == 2:
					for item in its:
						if any(stays):
							item["end"] = (datetime.fromisoformat(item["end"]) + timedelta(minutes=stays[-1])).isoformat()
							item["start"] = departure if stays[0] else item.get("start", departure)
							item["displayDurationSeconds"] = (datetime.fromisoformat(item["end"]) - datetime.fromisoformat(item["start"])).total_seconds()
						item["stopStayMinutes"] = stays
					opts = [normalize(it, stops, departure) for it in its[:d.get("maxResults", 6)]]
					break
				it = its[0]
				trips.append(trip_summary(it, len(legs), cursor))
				legs.extend(it["legs"])
				total += it.get("displayDurationSeconds", it.get("duration", 0))
				gaps.extend(it.get("accessGaps", []))
				warnings.extend(it.get("planningWarnings", []))
				cursor = it["end"]
			if not opts:
				cursor = (datetime.fromisoformat(cursor) + timedelta(minutes=stays[-1])).isoformat()
				journey_start = departure if stays[0] else trips[0]["start"]
				total = (datetime.fromisoformat(cursor) - datetime.fromisoformat(journey_start)).total_seconds()
				opts = [normalize(dict(legs=legs, trips=trips, stopStayMinutes=stays, start=journey_start,
					duration=total, displayDurationSeconds=total, end=cursor,
					walkDistance=sum(l.get("distance", 0) for l in legs if l["mode"] == "WALK"),
					transfers=max(0, sum(bool(l.get("transitLeg")) and not l.get("interlineWithPreviousLeg", False) for l in legs) - 1),
					accessGaps=gaps, hasUnverifiedAccess=bool(gaps), planningWarnings=list(dict.fromkeys(warnings))), stops, departure)]
		else:
			start = datetime.fromisoformat(departure)
			end = min(
				start + timedelta(hours=12), router.service_window()[1] - timedelta(minutes=1)
			)
			payload = dict(
				planningMode="full",
				jobs=[dict(s, id=str(i), serviceMinutes=stays[i]) for i, s in enumerate(stops)],
				workers=[dict(id="traveller", start=start.isoformat(), end=end.isoformat())],
				modes=modes,
				serviceMinutes=0,
				maxRuntimeSeconds=600,
				maxRounds=5,
				solverSeconds=10,
			)
			oj = manager.start(payload)["id"]
			while True:
				state = manager.get(oj)
				if state["events"]:
					progress(j, state["events"][-1]["message"])
				if state["status"] != "running":
					break
				time.sleep(1)
			result = state.get("result")
			if not result or not result.get("feasible"):
				raise ValueError(
					state.get("error")
					or (result or {}).get("message")
					or "No validated route was found."
				)
			events = result["schedules"][0]["events"]
			ordered = [stops[int(e["jobId"])] for e in events if e["kind"] == "job"]
			ordered_stays = [stays[int(e["jobId"])] for e in events if e["kind"] == "job"]
			legs = [
				l
				for e in events
				if e["kind"] == "travel"
				for l in (e.get("itinerary") or {}).get("legs", [])
			]
			trips = []
			leg_start = 0
			for event in events:
				if event["kind"] != "travel":
					continue
				itinerary = event.get("itinerary")
				if not itinerary:
					raise ValueError("A point-to-point itinerary is missing. Please plan again.")
				trips.append(trip_summary(itinerary, leg_start, event.get("start", itinerary.get("start", departure))))
				leg_start += len(itinerary["legs"])
			if len(trips) != len(ordered) - 1:
				raise ValueError("The visit order does not match the point-to-point itineraries.")
			journey_start = departure if ordered_stays[0] else trips[0]["start"]
			total = (datetime.fromisoformat(result["schedules"][0]["finish"]) - datetime.fromisoformat(journey_start)).total_seconds()
			itineraries = [e["itinerary"] for e in events if e.get("itinerary")]
			gaps = [gap for it in itineraries for gap in it.get("accessGaps", [])]
			opts = [
				normalize(
					dict(legs=legs, trips=trips, stopStayMinutes=ordered_stays, start=journey_start, duration=total, displayDurationSeconds=total,
						end=result["schedules"][0]["finish"],
						walkDistance=sum(it.get("walkDistance", 0) for it in itineraries),
						transfers=max(0, sum(bool(l.get("transitLeg")) and not l.get("interlineWithPreviousLeg", False) for l in legs) - 1),
						accessGaps=gaps, hasUnverifiedAccess=bool(gaps),
						planningWarnings=list(dict.fromkeys(w for it in itineraries for w in it.get("planningWarnings", [])))),
					ordered,
					departure,
				)
			]
			opts[0]["optimisationNote"] = (
				"Optimised free start/end order; global optimality is not guaranteed."
			)
		with storage.db() as c:
			c.execute(
				"UPDATE jobs SET state='succeeded',result=?,progress='Ready' WHERE id=?",
				(json.dumps(opts, ensure_ascii=False), j),
			)
			owner = c.execute("SELECT user_id FROM jobs WHERE id=?", (j,)).fetchone()[0]
			c.execute(
				"INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)",
				(
					owner,
					j,
					"history",
					json.dumps(dict(id=j, routes=opts, created=time.time())),
					time.time(),
				),
			)
			if cloud.ENABLED:
				c.execute(
					"INSERT OR REPLACE INTO cloud_outbox VALUES(?,?,?)",
					(
						owner,
						j,
						json.dumps(
							dict(
								id=j,
								routes=opts,
								channel=d.get("channel", "explore"),
								created=time.time(),
							)
						),
					),
				)
	except Exception as e:
		with storage.db() as c:
			c.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (str(e), j))


def start(uid, d):
	clean = validate(d)
	key = str(d.get("key") or secrets.token_hex(16))[:100]
	with storage.db() as c:
		old = c.execute("SELECT id FROM jobs WHERE user_id=? AND key=?", (uid, key)).fetchone()
		if old:
			return dict(id=old["id"])
		if (
			c.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]
			>= 2
		):
			raise ValueError("Two route jobs are already running. Please wait.")
		j = secrets.token_hex(16)
		c.execute(
			"INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
			(j, uid, key, "queued", json.dumps(clean), None, None, "Waiting to start", time.time()),
		)
	threading.Thread(target=run, args=(j, clean), daemon=True).start()
	return dict(id=j)


def job(uid, j):
	with storage.db() as c:
		r = c.execute("SELECT * FROM jobs WHERE user_id=? AND id=?", (uid, j)).fetchone()
	if not r:
		raise KeyError("Unknown job")
	return dict(
		id=j,
		channel=json.loads(r["payload"]).get("channel", "explore"),
		state=r["state"],
		progress=r["progress"],
		error=r["error"],
		routes=json.loads(r["result"]) if r["result"] else None,
	)
