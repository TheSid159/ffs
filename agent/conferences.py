"""Reference lists of major oncology/urology meetings and clinical trial
registries worldwide.

These ground two of the research prompt's search categories (conference
highlights, new trial registrations) with a concrete "what counts as major"
list, instead of leaving Claude to search blind for what a major oncology
meeting or registry even is. Not date-driven — timing shifts year to year,
so `typical_timing` is just a rough hint Claude can use when judging whether
a meeting is imminent, not something the code schedules against.
"""

MAJOR_ONCOLOGY_MEETINGS = [
    {"name": "ASCO Annual Meeting", "region": "USA", "typical_timing": "May/June"},
    {"name": "ASCO GU (Genitourinary Cancers Symposium)", "region": "USA", "typical_timing": "January/February"},
    {"name": "ASCO GI (Gastrointestinal Cancers Symposium)", "region": "USA", "typical_timing": "January"},
    {"name": "ESMO Congress", "region": "Europe", "typical_timing": "September/October"},
    {"name": "ESMO Immuno-Oncology Congress", "region": "Europe", "typical_timing": "December"},
    {"name": "AACR Annual Meeting", "region": "USA", "typical_timing": "April"},
    {"name": "ASH Annual Meeting (American Society of Hematology)", "region": "USA", "typical_timing": "December"},
    {"name": "ASTRO Annual Meeting", "region": "USA", "typical_timing": "September/October"},
    {"name": "SITC Annual Meeting (Society for Immunotherapy of Cancer)", "region": "USA", "typical_timing": "November"},
    {"name": "AUA Annual Meeting (American Urological Association)", "region": "USA", "typical_timing": "April/May"},
    {"name": "EAU Annual Congress (European Association of Urology)", "region": "Europe", "typical_timing": "March"},
    {"name": "WCLC (World Conference on Lung Cancer)", "region": "Global", "typical_timing": "September"},
    {"name": "SABCS (San Antonio Breast Cancer Symposium)", "region": "USA", "typical_timing": "December"},
    {"name": "CSCO Annual Meeting (Chinese Society of Clinical Oncology)", "region": "China", "typical_timing": "September"},
    {"name": "JSMO Annual Meeting (Japanese Society of Medical Oncology)", "region": "Japan", "typical_timing": "July"},
]

IMAGING_AND_CLINOPS_MEETINGS = [
    {"name": "SNMMI Annual Meeting (Society of Nuclear Medicine and Molecular Imaging)", "region": "USA", "typical_timing": "June"},
    {"name": "RSNA Annual Meeting (Radiological Society of North America)", "region": "USA", "typical_timing": "November/December"},
    {"name": "DIA Global Annual Meeting (Drug Information Association)", "region": "USA", "typical_timing": "June"},
    {"name": "SCOPE Summit (Summit for Clinical Ops Executives)", "region": "USA", "typical_timing": "February"},
]

TRIAL_REGISTRIES = [
    {"name": "ClinicalTrials.gov", "region": "USA / accepts global trials"},
    {"name": "EU Clinical Trials Information System (CTIS)", "region": "European Union"},
    {"name": "WHO International Clinical Trials Registry Platform (ICTRP)", "region": "Global aggregator"},
    {"name": "ISRCTN Registry", "region": "UK / global"},
    {"name": "Chinese Clinical Trial Registry (ChiCTR)", "region": "China"},
    {"name": "Japan Registry of Clinical Trials (jRCT)", "region": "Japan"},
    {"name": "Clinical Trials Registry - India (CTRI)", "region": "India"},
    {"name": "Australian New Zealand Clinical Trials Registry (ANZCTR)", "region": "Australia / New Zealand"},
]


def format_meeting_list() -> str:
    return "\n".join(f"   - {m['name']} ({m['region']}, typically {m['typical_timing']})" for m in MAJOR_ONCOLOGY_MEETINGS)


def format_imaging_clinops_meeting_list() -> str:
    return "\n".join(f"   - {m['name']} ({m['region']}, typically {m['typical_timing']})" for m in IMAGING_AND_CLINOPS_MEETINGS)


def format_registry_list() -> str:
    return "\n".join(f"   - {r['name']} ({r['region']})" for r in TRIAL_REGISTRIES)
