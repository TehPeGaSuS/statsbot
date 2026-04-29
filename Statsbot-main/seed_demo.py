#!/usr/bin/env python3
"""Seed demo data for testing the dashboard."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import random
import yaml
from database.models import *
from bot.sensors import Sensors

init_db()

with open("config/config.yml") as f:
    config = yaml.safe_load(f)

sensors = Sensors(config, "libera")

nicks_data = [
    ("Zaphod",         "zaphod!z@betelgeuse.com"),
    ("Trillian",       "trillian!t@earth.space"),
    ("Ford",           "ford!f@betelgeuse.com"),
    ("Marvin",         "marvin!m@heart-of-gold.ai"),
    ("Arthur",         "arthur!a@earth.com"),
    ("Slartibartfast", "slarti!s@magrathea.com"),
    ("Zarniwoop",      "zar!z@frogstar.net"),
    ("Hotblack",       "hot!h@disaster.area"),
]

messages = [
    "has anyone seen my towel?",
    "DON'T PANIC",
    "the answer is 42 :)",
    "I think I'm a robot, not sure though",
    "semi-gratuitous comment about the nature of fjords",
    "mostly harmless...",
    "time is an illusion, lunchtime doubly so",
    "what do you mean the Earth has been demolished? ;)",
    "share and enjoy! :D",
    "life, the universe, and everything",
    "the babel fish is a dead giveaway isn't it?",
    "i never could get the hang of thursdays",
    "oh freddled gruntbuggly...",
    "resistance is useless!",
    "we apologize for the inconvenience",
    "he was a dreamer, a thinker, a speculative philosopher... or a loony",
    "the ships hung in the sky in much the same way that bricks don't",
    "nothing travels faster than the speed of light except bad news",
    "a common mistake people make when trying to design foolproof systems",
    "reality is frequently inaccurate",
]

topics = [
    ("Ford",     "Hitchhiker Guide updates: Earth - mostly harmless"),
    ("Zaphod",   "DON'T PANIC | towels welcome"),
    ("Trillian", "42 | all your answers here"),
    ("Arthur",   "Tea? Anyone? Please?"),
]

print("Seeding demo data...")
for nick, host in nicks_data:
    nid = get_or_create_nick(nick, "libera", "#hitchhikers", host)
    n_lines = random.randint(80, 900)
    for _ in range(n_lines):
        msg = random.choice(messages)
        sensors.on_privmsg(nick, host, "#hitchhikers", msg)
    for _ in range(random.randint(1, 30)):
        sensors.on_join(nick, host, "#hitchhikers")

    # Simulate realistic hourly patterns
    with get_conn() as conn:
        for h in range(24):
            if 9 <= h <= 23:
                hl = random.randint(0, 60)
            elif 0 <= h <= 2:
                hl = random.randint(0, 20)
            else:
                hl = random.randint(0, 5)
            if hl:
                conn.execute(
                    "INSERT INTO hourly_activity(nick_id,hour,lines) VALUES(?,?,?) "
                    "ON CONFLICT(nick_id,hour) DO UPDATE SET lines=lines+?",
                    (nid, h, hl, hl)
                )
    print(f"  {nick}: {n_lines} lines")

for by, topic in topics:
    add_topic("libera", "#hitchhikers", topic, by)

ford_id  = get_or_create_nick("Ford",     "libera", "#hitchhikers")
zap_id   = get_or_create_nick("Zaphod",   "libera", "#hitchhikers")
tril_id  = get_or_create_nick("Trillian", "libera", "#hitchhikers")

add_url(ford_id,  "libera", "#hitchhikers", "https://hitchhikersguide.galaxy/earth")
add_url(zap_id,   "libera", "#hitchhikers", "https://heartofgold.ship/nav")
add_url(tril_id,  "libera", "#hitchhikers", "https://magrathea.com/custom-planets")

add_kick("libera", "#hitchhikers", "Zaphod",   "Marvin",     "stop being so depressing",          None)
add_kick("libera", "#hitchhikers", "Ford",      "Arthur",     "missed the bus",                    None)
add_kick("libera", "#hitchhikers", "Trillian",  "Zarniwoop",  "insufficient universe-hopping",     None)

update_peak("libera", "#hitchhikers", 0, len(nicks_data))

print("Demo data seeded!")
print(f"Users: {count_users('libera', '#hitchhikers')}")
print(f"Top 5 lines: {get_top('libera', '#hitchhikers', 'lines', 0, 5)}")
