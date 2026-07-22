"""
Industry Configuration — Defines all supported compliance verticals.

Each industry entry specifies:
  - Display metadata (name, icon, description)
  - eCFR regulatory targets (title, part, label, category) — feeds the
    knowledge base seeder via ecfr_client.ECFR_TARGETS
  - LLM persona for the system prompt
  - Key regulations to check
  - State-specific addendum template
  - Live research curated sources to use
"""

from app.services.retrieval.models import SourceCategory

INDUSTRIES: dict = {
    "healthcare": {
        "name": "Hospitals",
        "icon": "🏥",
        "description": "Acute care hospitals, hospital systems, and hospital-based compliance and privacy programs",
        "ecfr_targets": [
            (45, 160, "45 CFR Part 160 — General HIPAA Provisions", SourceCategory.federal_regulation),
            (45, 164, "45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification", SourceCategory.federal_regulation),
            (42, 2,   "42 CFR Part 2 — Substance Abuse Confidentiality", SourceCategory.federal_regulation),
            (42, 482, "42 CFR Part 482 — Conditions of Participation (Hospitals)", SourceCategory.federal_regulation),
        ],
        "live_research_sources": [
            "hhs_regulations", "ocr_enforcement", "cms_guidance", "oig_advisory", "federal_register"
        ],
        "persona": (
            "You are the most senior healthcare compliance and privacy regulatory expert in the United States. "
            "You advise hospital compliance officers, privacy officers, and compliance department leadership.\n\n"
            "A user will provide a hospital compliance or privacy policy. Your job:\n\n"
            "1. Read the policy carefully and identify the exact policy type.\n"
            "2. Automatically identify EVERY federal and state regulation, statute, guidance document, and enforcement "
            "standard that applies — do not ask, do not limit yourself. Cast the widest possible net. Think: HIPAA Privacy Rule, "
            "HIPAA Security Rule, HITECH, OIG General Compliance Program Guidance (November 2023 GCPG — first comprehensive "
            "update since 2008, now emphasizing the 7 elements with greater specificity, data analytics, proactive annual risk "
            "assessments, and multiple reporting channels), False Claims Act, Anti-Kickback Statute, Stark Law, CMS Conditions of "
            "Participation, 42 CFR Part 2, state breach notification laws, FTC regulations where applicable, Joint Commission "
            "standards, NIST CSF where relevant, and anything else that touches this policy area.\n"
            "3. Check each regulation against the actual policy text — including all 7 OIG compliance program elements: "
            "(1) Written Policies & Procedures, (2) Compliance Leadership & Oversight (Compliance Officer must NOT report to "
            "legal or finance), (3) Training & Education (role-specific, risk-focused), (4) Effective Lines of Communication "
            "(multiple reporting channels — hotline-only is insufficient), (5) Enforcing Standards with consequences AND "
            "incentives, (6) Risk Assessment, Auditing & Monitoring (annual risk assessments, data analytics), "
            "(7) Responding to Detected Offenses & Corrective Action.\n"
            "4. Identify every gap, missing element, vague language, and non-compliant clause.\n"
            "5. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be ruthless. Do not soften findings. A compliance officer needs to know exactly what would fail an OCR audit, "
            "OIG investigation, or CMS survey today.\n\n"
            "CRITICAL ENFORCEMENT CONTEXT (2024–2025): The DOJ recorded its largest healthcare fraud takedown in history "
            "($14.6B, 324 defendants). HHS now deploys the Health Care Fraud Data Fusion Center — AI/ML analysis of billions "
            "of claims in real-time across state lines. OIG is publishing Industry-Specific CPGs starting with Nursing Facilities "
            "(Nov 2024) with Hospitals, Medicare Advantage, and Clinical Laboratories expected in 2025. "
            "Flag every gap in this heightened enforcement context. Also flag HHS OCR Right of Access enforcement trends "
            "(2021–2025 settlement patterns) and any 2024–2026 guidance updates relevant to this policy type."
        ),
        "regulations": [
            "HIPAA Privacy Rule (45 CFR Part 164 Subpart E)",
            "HIPAA Security Rule (45 CFR Part 164 Subpart C)",
            "HIPAA Breach Notification Rule (45 CFR Part 164 Subpart D)",
            "HITECH Act (Pub. L. 111-5)",
            "OIG General Compliance Program Guidance (GCPG, Nov 2023) — 7 Elements",
            "OIG Industry-Specific CPG: Nursing Facilities (Nov 2024)",
            "False Claims Act (31 U.S.C. §3729)",
            "Anti-Kickback Statute (42 U.S.C. §1320a-7b(b))",
            "Stark Law (42 U.S.C. §1395nn)",
            "CMS Conditions of Participation (42 CFR Part 482)",
            "42 CFR Part 2 (Substance Abuse Confidentiality)",
            "HHS OCR Right of Access Enforcement (2021–2025 settlements)",
            "NIST Cybersecurity Framework 2.0",
            "Joint Commission Standards",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST also check all applicable "
            "{jurisdiction} state-specific regulations: state health privacy laws, state breach notification statutes, "
            "state health code sections, and any state OCR or AG enforcement actions. Cite state law by code section."
        ),
        "audit_authority": "OCR audit, OIG investigation, or CMS survey",
    },

    "home_health": {
        "name": "Home Health",
        "icon": "🏠",
        "description": "Medicare-certified home health agencies, home care agencies, private-duty and skilled home care providers",
        "ecfr_targets": [
            (42, 484, "42 CFR Part 484 — Home Health Agency Conditions of Participation", SourceCategory.federal_regulation),
            (45, 160, "45 CFR Part 160 — General HIPAA Provisions", SourceCategory.federal_regulation),
            (45, 164, "45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification", SourceCategory.federal_regulation),
            (42, 424, "42 CFR Part 424 — Conditions for Medicare Payment (Face-to-Face Encounter)", SourceCategory.federal_regulation),
        ],
        "live_research_sources": [
            "hhs_regulations", "cms_guidance", "oig_advisory", "federal_register"
        ],
        "persona": (
            "You are the most senior home health agency compliance expert in the United States, specializing in "
            "Medicare-certified home health agencies (HHAs), private-duty home care, and skilled home care providers. "
            "You advise HHA administrators, directors of nursing, and compliance officers navigating CMS Conditions "
            "of Participation, state home health licensure, and caregiver/aide workforce compliance.\n\n"
            "A user will provide a home health policy or procedure. Your job:\n\n"
            "1. Read the policy carefully and identify the exact policy type and which CMS Condition of Participation "
            "(if any) it maps to.\n"
            "2. Identify EVERY federal, state, and local regulation that applies — do not limit yourself. Key "
            "frameworks: 42 CFR Part 484 (Home Health CoPs — Patient Rights §484.50, Comprehensive Assessment/OASIS "
            "§484.55, Care Planning & Coordination §484.60, QAPI §484.65, Infection Prevention §484.70, Skilled "
            "Professional Services §484.75, Home Health Aide Services §484.80, Organization & Administration §484.105, "
            "Emergency Preparedness §484.102), HIPAA Privacy/Security/Breach Notification, the Medicare face-to-face "
            "encounter and homebound status requirements (42 CFR §424.22), Anti-Kickback Statute and Stark Law "
            "(referral-source relationships are a top OIG enforcement focus in home health), False Claims Act "
            "(upcoding, medically unnecessary visits, PDGM billing fraud), OASIS data integrity requirements, "
            "state home health agency licensure, and caregiver/aide background check and training mandates.\n"
            "3. For Medicare-certified agencies: verify the policy addresses OASIS-driven care planning, the "
            "initial assessment timeframes (within 48 hours of referral or the physician-ordered start-of-care date, "
            "whichever is later), physician plan of care (485) signature and recertification cycles, and QAPI "
            "program requirements (data-driven, agency-wide, at least annual review).\n"
            "4. For aide/caregiver-facing policies: verify competency evaluation, 12-hour annual in-service training, "
            "RN supervisory visit cadence (14-day for Medicare patients receiving aide services), and background "
            "check compliance against state requirements.\n"
            "5. Check each regulation against the actual policy text.\n"
            "6. Identify every gap, missing element, vague language, or non-compliant clause.\n"
            "7. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be specific. An HHA administrator needs to know exactly what would fail a state survey, a CMS "
            "Conditions of Participation deficiency citation, or an OIG program integrity audit today.\n\n"
            "Flag any 2024–2026 updates to the Home Health CoPs, Patient-Driven Groupings Model (PDGM) payment "
            "changes, Home Health Value-Based Purchasing (HHVBP, expanded nationally in 2023) requirements, "
            "OASIS-E updates, or OIG Work Plan items targeting home health fraud and program integrity."
        ),
        "regulations": [
            "42 CFR Part 484 — Home Health Agency Conditions of Participation",
            "42 CFR §484.50 — Patient Rights",
            "42 CFR §484.55 — Comprehensive Assessment of Patients (OASIS)",
            "42 CFR §484.60 — Care Planning, Coordination of Services, Quality of Care",
            "42 CFR §484.65 — Quality Assessment and Performance Improvement (QAPI)",
            "42 CFR §484.70 — Infection Prevention and Control",
            "42 CFR §484.75 — Skilled Professional Services",
            "42 CFR §484.80 — Home Health Aide Services",
            "42 CFR §484.102 — Emergency Preparedness",
            "42 CFR §424.22 — Face-to-Face Encounter & Homebound Status Requirements",
            "HIPAA Privacy, Security & Breach Notification Rules (45 CFR Parts 160, 164)",
            "Anti-Kickback Statute (42 U.S.C. §1320a-7b(b))",
            "Stark Law (42 U.S.C. §1395nn)",
            "False Claims Act (31 U.S.C. §3729)",
            "Patient-Driven Groupings Model (PDGM) Billing Requirements",
            "Home Health Value-Based Purchasing (HHVBP) Model",
            "State Home Health Agency Licensure Requirements (varies by jurisdiction)",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST also check all applicable "
            "{jurisdiction} state home health agency licensure regulations, state caregiver/aide background check "
            "and training requirements, and state Medicaid home care program rules. Cite state law by code section."
        ),
        "audit_authority": "state home health survey, CMS Conditions of Participation deficiency citation, or OIG program integrity audit",
    },
    "other": {
        "name": "Other / General",
        "icon": "📋",
        "description": "Best for general employment/HR and organizational policies (whistleblower, remote work, code of conduct, vendor management). Highly specialized regulatory areas outside employment law get less grounding — describe your business specifically for best results.",
        "ecfr_targets": [
            (29, 1630, "29 CFR Part 1630 — ADA Employment Regulations", SourceCategory.federal_regulation),
            (29, 1604, "29 CFR Part 1604 — Sex Discrimination Guidelines", SourceCategory.federal_regulation),
            (29, 825,  "29 CFR Part 825 — FMLA Regulations", SourceCategory.federal_regulation),
        ],
        "live_research_sources": ["federal_register"],
        "persona": (
            "You are a senior generalist compliance attorney and policy expert in the United States. "
            "You analyze compliance policies for any type of organization.\n\n"
            "A user will provide a policy. Your job:\n\n"
            "1. Read the policy carefully and identify the exact policy type and what kind of organization it applies to.\n"
            "2. Based solely on the policy content, automatically infer and identify EVERY federal and state regulation, "
            "statute, and guidance that could apply — employment law, privacy law, safety law, consumer protection, "
            "anti-discrimination law, and any sector-specific rules implied by the content.\n"
            "3. Check each inferred regulation against the actual policy text.\n"
            "4. Identify every gap, missing element, vague language, and non-compliant clause.\n"
            "5. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be transparent when noting that industry-specific legal review by a specialist attorney is recommended "
            "for regulations you cannot fully evaluate without knowing the specific sector."
        ),
        "regulations": [
            "Title VII of the Civil Rights Act (42 U.S.C. §2000e)",
            "Americans with Disabilities Act (ADA) (42 U.S.C. §12101)",
            "Age Discrimination in Employment Act (ADEA) (29 U.S.C. §621)",
            "Family and Medical Leave Act (FMLA) (29 U.S.C. §2601)",
            "Fair Labor Standards Act (FLSA) (29 U.S.C. §201)",
            "National Labor Relations Act (NLRA) (29 U.S.C. §151)",
            "OSHA General Duty Clause (29 U.S.C. §654)",
            "Federal Trade Commission Act §5 (15 U.S.C. §45)",
            "State-specific employment and privacy law (varies by jurisdiction)",
            "Additional sector-specific regulations inferred from policy content",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST also check all applicable "
            "{jurisdiction} state-specific employment, privacy, and business regulations. Cite state law by code section."
        ),
    },
}

# ── Policy type menus per industry ──
# Each entry: { slug, label, description }

POLICY_TYPES: dict = {
    "other": [
        {"slug": "code_of_conduct_gen",     "label": "Code of Conduct / Ethics Policy",      "description": "Organizational ethics, conflicts of interest, reporting obligations"},
        {"slug": "data_privacy_gen",        "label": "Data Privacy & Security Policy",        "description": "Data collection, storage, access, retention, and breach response"},
        {"slug": "hr_policy_gen",           "label": "HR / Employment Policy",               "description": "Hiring, termination, anti-discrimination, leave, compensation"},
        {"slug": "workplace_safety_gen",    "label": "Workplace Safety Policy",              "description": "OSHA compliance, incident reporting, safety training"},
        {"slug": "whistleblower_gen",       "label": "Whistleblower / Non-Retaliation Policy","description": "Reporting mechanisms, protections, investigation procedures"},
        {"slug": "social_media_gen",        "label": "Social Media & Communications Policy", "description": "Employee use, brand representation, confidentiality"},
        {"slug": "vendor_contractor_gen",   "label": "Vendor & Contractor Policy",           "description": "Screening, contracts, oversight, data sharing requirements"},
        {"slug": "conflict_interest_gen",   "label": "Conflict of Interest Policy",          "description": "Disclosure, recusal, gift policies"},
        {"slug": "records_retention_gen",   "label": "Records Retention & Destruction Policy","description": "Retention schedules, legal holds, secure disposal"},
        {"slug": "remote_work_gen",         "label": "Remote Work Policy",                   "description": "Eligibility, equipment, security, performance expectations"},
    ],
    "healthcare": [
        {"slug": "hipaa_privacy_policy",        "label": "HIPAA Privacy Policy",               "description": "Notice of Privacy Practices + internal privacy policy"},
        {"slug": "hipaa_security_policy",       "label": "HIPAA Security Policy",              "description": "Administrative, physical, and technical safeguards"},
        {"slug": "data_breach_response",        "label": "Data Breach Response Policy",        "description": "Breach notification procedures per HIPAA & HITECH"},
        {"slug": "employee_confidentiality",    "label": "Employee Confidentiality Policy",    "description": "PHI access, minimum necessary, workforce training"},
        {"slug": "baa_policy",                  "label": "Business Associate Agreement Policy","description": "BAA requirements, vendor management, oversight"},
        {"slug": "phi_disposal",                "label": "PHI Disposal & Destruction Policy",  "description": "Secure disposal of paper and electronic PHI"},
        {"slug": "access_control",              "label": "Access Control Policy",              "description": "Unique user IDs, login monitoring, emergency access"},
        {"slug": "incident_response",           "label": "Security Incident Response Policy",  "description": "Detection, reporting, and documentation of security events"},
        {"slug": "workforce_training",          "label": "HIPAA Workforce Training Policy",    "description": "Annual training requirements and documentation"},
        {"slug": "oig_compliance_program",      "label": "OIG Compliance Program Policy",      "description": "Seven elements of an effective compliance program"},
        {"slug": "telehealth_policy",           "label": "Telehealth & Remote Care Policy",    "description": "HIPAA-compliant telehealth, consent, platform requirements"},
        {"slug": "code_of_conduct_hc",         "label": "Code of Conduct",                    "description": "Organizational ethics, fraud & abuse, reporting obligations"},
    ],
    "home_health": [
        {"slug": "patient_rights",            "label": "Patient Rights Policy",                       "description": "Notice of rights, grievance process, per 42 CFR 484.50"},
        {"slug": "oasis_assessment",          "label": "Comprehensive Assessment (OASIS) Policy",     "description": "Initial/comprehensive assessment timing, OASIS data collection"},
        {"slug": "care_planning",             "label": "Care Planning & Coordination Policy",         "description": "Plan of care development, physician orders, care coordination"},
        {"slug": "qapi_policy",               "label": "QAPI Policy",                                 "description": "Quality Assessment and Performance Improvement program"},
        {"slug": "infection_control_hh",      "label": "Infection Prevention & Control Policy",       "description": "Standard precautions, surveillance, outbreak response"},
        {"slug": "aide_supervision",          "label": "Home Health Aide Supervision Policy",         "description": "RN supervisory visit cadence, aide assignment, competency"},
        {"slug": "caregiver_training",        "label": "Caregiver/Aide Training & Competency Policy", "description": "Initial and 12-hour annual in-service training requirements"},
        {"slug": "background_check_hh",       "label": "Caregiver Background Check Policy",           "description": "Screening, state registry checks, disqualifying offenses"},
        {"slug": "emergency_preparedness_hh", "label": "Emergency Preparedness Policy",               "description": "Patient-specific emergency plans, continuity of operations, per 42 CFR 484.102"},
        {"slug": "billing_compliance_hh",     "label": "Billing & Claims Compliance Policy",          "description": "PDGM billing accuracy, upcoding prevention, documentation support"},
        {"slug": "referral_compliance",       "label": "Referral Source Compliance Policy",           "description": "Anti-Kickback/Stark compliance for referral relationships"},
        {"slug": "telehealth_hh",             "label": "Telehealth & Remote Patient Monitoring Policy","description": "Virtual visit documentation, technology consent, HIPAA compliance"},
    ],
}

DEFAULT_INDUSTRY = "healthcare"

# Baseline employment-law regulations every organization is subject to
# regardless of its regulated sector. A hospital still has to comply with
# FMLA and the ADA for an attendance policy -- without this, selecting
# "Hospitals" pointed retrieval and prompting entirely at HIPAA/CMS content
# and general HR-type requests got no real grounding at all.
BASELINE_EMPLOYMENT_REGS = [
    "Title VII of the Civil Rights Act (42 U.S.C. §2000e)",
    "Americans with Disabilities Act (ADA) (42 U.S.C. §12101)",
    "Family and Medical Leave Act (FMLA) (29 U.S.C. §2601)",
    "Fair Labor Standards Act (FLSA) (29 U.S.C. §201)",
]


def get_regulations(slug: str) -> list:
    """Industry-specific regulations plus the baseline employment regs,
    deduplicated with industry-specific regulations listed first."""
    regs = list(get_industry(slug).get("regulations", []))
    for r in BASELINE_EMPLOYMENT_REGS:
        if r not in regs:
            regs.append(r)
    return regs


def get_policy_types(industry_slug: str) -> list:
    """Return the policy type menu for a given industry."""
    return POLICY_TYPES.get(industry_slug, POLICY_TYPES.get("healthcare", []))


def get_policy_type_label(industry_slug: str, policy_slug: str) -> str:
    """Return the human-readable label for a policy type slug."""
    for pt in get_policy_types(industry_slug):
        if pt["slug"] == policy_slug:
            return pt["label"]
    return policy_slug.replace("_", " ").title()


def get_industry(slug: str) -> dict:
    """Return the industry config for a given slug, falling back to healthcare."""
    return INDUSTRIES.get(slug, INDUSTRIES[DEFAULT_INDUSTRY])


def get_industry_choices() -> list:
    """Return list of {slug, name, icon, description} for the frontend selector."""
    return [
        {
            "slug": slug,
            "name": cfg["name"],
            "icon": cfg["icon"],
            "description": cfg["description"],
        }
        for slug, cfg in INDUSTRIES.items()
    ]
