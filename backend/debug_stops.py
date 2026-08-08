"""Scratch: why is Yerba Buena -> Palo Alto not verified?"""
from slackline.core import constants
from slackline.schedule.index import ScheduleIndex

ix = ScheduleIndex("tests/fixtures/schedule_index_fixture.sqlite")
YERBA = (37.7850, -122.4020)
DINNER = (37.4429, -122.1614)

print("stops near Yerba Buena (2.0 km, limit 8):")
froms = ix.stops_near(*YERBA, radius_km=2.0, limit=8)
for s in froms:
    print(f"  {s.agency} {s.stop_id} {s.name}")
print("stops near dinner anchor:")
tos = ix.stops_near(*DINNER, radius_km=2.0, limit=8)
for s in tos:
    print(f"  {s.agency} {s.stop_id} {s.name}")

j = ix.next_journey(froms, tos, "2026-08-12", 17 * 60, constants.TRANSFER_BUFFER_MIN)
print("journey from ALL froms:", j)
for f in froms:
    j1 = ix.next_journey([f], tos, "2026-08-12", 17 * 60, constants.TRANSFER_BUFFER_MIN)
    print(f"  from {f.name}: {'None' if not j1 else [(r.route, r.dep_min, r.arr_min) for r in j1]}")
