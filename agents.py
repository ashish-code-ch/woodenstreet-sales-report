"""
agents.py
=========
Master agent lookup table for WoodenStreet call analysis.
Maps the short name + location code used in transcript filenames
to full name, team, location, and status.

Sources:
  - BD sales team.jpeg
  - customer support udaipur team.jpeg
  - agentList.xlsx

Filename pattern:  BD Sales - {ShortName} {LOC}-all_diarized.json
Location codes:    JPR = Jaipur | UDR = Udaipur | BLR = Bangalore | HSR = HSR Layout (Bangalore)
"""

LOCATION_CODE_MAP = {
    "JPR": "Jaipur",
    "UDR": "Udaipur",
    "BLR": "Bangalore",
    "HSR": "HSR Layout",
}

# ── BD Sales Agent Lookup ──────────────────────────────────────────────────────
# Key  : "{ShortName} {LOC}" exactly as it appears in the transcript filename
# Value: full agent details

BD_SALES_AGENTS = {

    # ── Udaipur ──────────────────────────────────────────────────────────────
    "Gunjan UDR":       {"full_name": "Gunjan Joshi",         "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Mayank UDR":       {"full_name": "Mayank Vyas",           "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Abhishek UDR":     {"full_name": "Abhishek Choubey",      "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Siddhartha UDR":   {"full_name": "Siddhartha Sharma",     "location": "Udaipur",    "team": "BD Sales", "status": "Notice"},
    "Sambhav UDR":      {"full_name": "Sambhav Mehta",         "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Prachi UDR":       {"full_name": "Prachi Vaishnav",       "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Anushree UDR":     {"full_name": "Anushree Jain",         "location": "Udaipur",    "team": "BD Sales", "status": "Notice"},
    "Hardik UDR":       {"full_name": "Hardik Audichya",       "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Vishal Tak UDR":   {"full_name": "Vishal Tak",            "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Utkarsh UDR":      {"full_name": "Utkarsh",               "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Rachit UDR":       {"full_name": "Rachit",                "location": "Udaipur",    "team": "BD Sales", "status": "Active"},
    "Kajal K UDR":      {"full_name": "Kajal K",               "location": "Udaipur",    "team": "BD Sales", "status": "Active"},

    # ── Jaipur ───────────────────────────────────────────────────────────────
    "Nitesh JPR":       {"full_name": "Nitesh Kaushik",        "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Sachin JPR":       {"full_name": "Sachin Gera",           "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Dushyant JPR":     {"full_name": "Dushyant Singh",        "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Tapish JPR":       {"full_name": "Tapish Shekhawat",      "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Rajeshwari JPR":   {"full_name": "Rajeshwari Shekhawat",  "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Kunal Aithani JPR":{"full_name": "Kunal Aithani",         "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Kunal JPR":        {"full_name": "Kunal Singh",           "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Zahid JPR":        {"full_name": "Zahid",                 "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Aman JPR":         {"full_name": "Aman",                  "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Kuldeep JPR":      {"full_name": "Kuldeep",               "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Labhansh JPR":     {"full_name": "Labhansh",              "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Manish JPR":       {"full_name": "Manish",                "location": "Jaipur",     "team": "BD Sales", "status": "Active"},
    "Tanishq JPR":      {"full_name": "Tanishq",               "location": "Jaipur",     "team": "BD Sales", "status": "Active"},

    # ── Bangalore ─────────────────────────────────────────────────────────────
    "Aryan BLR":        {"full_name": "Aryan Deep",            "location": "Bangalore",  "team": "BD Sales", "status": "Active"},
    "Subhra BLR":       {"full_name": "Subhra Toppo",          "location": "Bangalore",  "team": "BD Sales", "status": "Active"},
    "Nithin BLR":       {"full_name": "Nithin Belliappa PK",   "location": "Bangalore",  "team": "BD Sales", "status": "Active"},
    "Anjali BLR":       {"full_name": "Anjali Menon",          "location": "Bangalore",  "team": "BD Sales", "status": "Active"},
    "Ronak BLR":        {"full_name": "Ronak Mohanty",         "location": "Bangalore",  "team": "BD Sales", "status": "Active"},
    "Chandrima BLR":    {"full_name": "Chandrima",             "location": "Bangalore",  "team": "BD Sales", "status": "Active"},

    # ── HSR Layout (Bangalore) ────────────────────────────────────────────────
    "Anamika HSR":      {"full_name": "Anamika Kumar",         "location": "HSR Layout", "team": "BD Sales", "status": "Active"},
    "K Babu HSR":       {"full_name": "K Babu",                "location": "HSR Layout", "team": "BD Sales", "status": "Active"},
    "Priyanka HSR":     {"full_name": "Priyanka",              "location": "HSR Layout", "team": "BD Sales", "status": "Active"},
}

# ── Customer Support Agents (Udaipur) ─────────────────────────────────────────
SUPPORT_AGENTS = {
    # Support Leads
    "W124012": {"full_name": "Vatsal Joshi",              "location": "Udaipur", "team": "Customer Support", "role": "Support-Lead"},
    "W123882": {"full_name": "Vishal Singh Rajawat",      "location": "Udaipur", "team": "Customer Support", "role": "Support-Lead"},
    "W12392":  {"full_name": "Garima Jhala",              "location": "Udaipur", "team": "Customer Support", "role": "Support-Lead"},

    # Inbound
    "W123685": {"full_name": "Viresh Soni",               "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124143": {"full_name": "Khushi Sharma",             "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124361": {"full_name": "Kashish Sahu",              "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124367": {"full_name": "Goutam Lodha",              "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124405": {"full_name": "Anas Ahmed Khan",           "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124861": {"full_name": "Anshu Bhagya",              "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124873": {"full_name": "Diksha Sanadhya",           "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124914": {"full_name": "Lavish Choubsia",           "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124876": {"full_name": "Kunal Pandey",              "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},
    "W124842": {"full_name": "Tushar Mali",               "location": "Udaipur", "team": "Customer Support", "role": "Inbound"},

    # Outbound
    "W124371": {"full_name": "Jyotish Prakash Audichya",  "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W123954": {"full_name": "Aakash Wadhwani",           "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W12972":  {"full_name": "Md. Shahrukh",              "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124236": {"full_name": "Shakir Mohammad",           "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124872": {"full_name": "Mahaveer Singh",            "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124224": {"full_name": "Jash Valecha",              "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124826": {"full_name": "Sheersh Upadhyaya",         "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124406": {"full_name": "Abhishek Singh Rao",        "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124387": {"full_name": "Lucky Lohar",               "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124992": {"full_name": "Hardik (OJT)",              "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W124101": {"full_name": "Sukhpreet Singh",           "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},
    "W123535": {"full_name": "Mukul Lohar",               "location": "Udaipur", "team": "Customer Support", "role": "Outbound"},

    # Carpentry
    "W122725": {"full_name": "Gauri Jhadav",              "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124404": {"full_name": "Manjeet Singh Rao",         "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124885": {"full_name": "Shamuill Fakhurddin Antri", "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W123674": {"full_name": "Neeraj Soni",               "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124479": {"full_name": "Niharika Sharma",           "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124061": {"full_name": "Shubham Shrimal",           "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124825": {"full_name": "Gajendra Singh",            "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124894": {"full_name": "Hitesh Regar",              "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
    "W124058": {"full_name": "Ali Abbas Beawer",          "location": "Udaipur", "team": "Customer Support", "role": "Carpentry"},
}

# ── Merged lookup (all agents) ────────────────────────────────────────────────
ALL_AGENTS = {**BD_SALES_AGENTS, **SUPPORT_AGENTS}


def resolve_agent(short_name: str, agent_id: str = None) -> dict:
    """
    Resolve an agent's full details from either:
      - short_name : "Sachin JPR"  (from BD Sales filename)
      - agent_id   : "W12307"      (from support system)

    Returns dict with: full_name, location, team, status/role
    Falls back gracefully if not found.
    """
    # Try explicit agent_id (support agents)
    if agent_id and agent_id in SUPPORT_AGENTS:
        return SUPPORT_AGENTS[agent_id]

    # Also check if short_name itself is an agent ID (e.g. "W124012")
    if short_name and short_name in SUPPORT_AGENTS:
        return SUPPORT_AGENTS[short_name]

    # Try short name (BD Sales agents)
    if short_name and short_name in BD_SALES_AGENTS:
        return BD_SALES_AGENTS[short_name]

    # Fallback — return what we know from the filename
    parts = (short_name or "").rsplit(" ", 1)
    name     = parts[0] if parts else short_name
    loc_code = parts[1] if len(parts) == 2 else ""
    return {
        "full_name": name,
        "location":  LOCATION_CODE_MAP.get(loc_code, loc_code),
        "team":      "BD Sales" if short_name else "Unknown",
        "status":    "Unknown",
    }


if __name__ == "__main__":
    print(f"BD Sales agents  : {len(BD_SALES_AGENTS)}")
    print(f"Support agents   : {len(SUPPORT_AGENTS)}")
    print()
    print("Sample lookups:")
    for key in ["Sachin JPR", "Anushree UDR", "Aryan BLR", "W124012", "W123882"]:
        print(f"  {key:20s} → {resolve_agent(key)}")
