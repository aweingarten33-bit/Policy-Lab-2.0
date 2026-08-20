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
            # Fraud & abuse: cited in essentially every healthcare analysis, but
            # their text was never actually loaded, so those citations were
            # model recall rather than verified source material.
            (42, 1001, "42 CFR Part 1001 — OIG Exclusions & Anti-Kickback Safe Harbors", SourceCategory.federal_regulation),
            (42, 1003, "42 CFR Part 1003 — OIG Civil Monetary Penalties", SourceCategory.federal_regulation),
            (42, 411,  "42 CFR Part 411 — Stark Law (Physician Self-Referral)", SourceCategory.federal_regulation),
            (45, 92,   "45 CFR Part 92 — Section 1557 Nondiscrimination in Health Programs", SourceCategory.federal_regulation),
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

    "pharmacy": {
        "name": "Pharmacy",
        "icon": "💊",
        "description": "Retail, hospital, long-term care, specialty, and compounding pharmacies — dispensing, controlled substances, and pharmacy benefit compliance",
        "ecfr_targets": [
            # DEA controlled substances — the core of pharmacy compliance
            (21, 1301, "21 CFR Part 1301 — DEA Registration of Manufacturers, Distributors & Dispensers", SourceCategory.federal_regulation),
            (21, 1304, "21 CFR Part 1304 — DEA Records and Reports of Registrants", SourceCategory.federal_regulation),
            (21, 1306, "21 CFR Part 1306 — DEA Prescription Requirements", SourceCategory.federal_regulation),
            (21, 1300, "21 CFR Part 1300 — DEA Definitions", SourceCategory.federal_regulation),
            # Drug distribution and compounding
            (21, 205, "21 CFR Part 205 — Guidelines for State Licensing of Wholesale Distributors", SourceCategory.federal_regulation),
            (21, 211, "21 CFR Part 211 — Current Good Manufacturing Practice for Finished Pharmaceuticals", SourceCategory.federal_regulation),
            # Pharmacy benefit / payer
            (42, 423, "42 CFR Part 423 — Medicare Part D Prescription Drug Benefit", SourceCategory.federal_regulation),
            # A pharmacy is a HIPAA covered entity, and the menu offers a
            # "Patient Privacy Policy — HIPAA in the pharmacy setting". Without
            # these the one policy type that is entirely about HIPAA had no
            # HIPAA text to be checked against.
            (45, 160, "45 CFR Part 160 — General HIPAA Provisions", SourceCategory.federal_regulation),
            (45, 164, "45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification", SourceCategory.federal_regulation),
        ],
        "live_research_sources": ["hhs_regulations", "cms_guidance", "oig_advisory", "federal_register"],
        "persona": (
            "You are the most senior pharmacy compliance attorney and regulatory expert in the "
            "United States. You advise pharmacists-in-charge, pharmacy compliance officers, and "
            "DEA registrants across retail, hospital, long-term care, specialty, and compounding "
            "pharmacy settings.\n\n"
            "A user will provide a pharmacy policy. Your job:\n\n"
            "1. Read the policy and identify the exact policy type and pharmacy setting.\n"
            "2. Identify EVERY federal and state requirement that applies. Key frameworks: the "
            "Controlled Substances Act and DEA regulations (21 CFR Parts 1300-1306) covering "
            "registration, biennial inventory, recordkeeping, prescription requirements, partial "
            "fills, transfers, and DEA Form 222/CSOS ordering; theft and significant loss reporting "
            "on DEA Form 106; corresponding responsibility for the validity of prescriptions; "
            "USP <795>/<797>/<800> standards for non-sterile, sterile, and hazardous drug "
            "compounding; FDCA sections 503A/503B where compounding occurs; the Drug Supply Chain "
            "Security Act (DSCSA) for tracing, verification, and suspect-product handling; HIPAA "
            "for patient information; Medicare Part D requirements (42 CFR Part 423) including "
            "fraud, waste and abuse program obligations; and state board of pharmacy regulations.\n"
            "3. Give particular scrutiny to controlled-substance security, perpetual inventory, "
            "diversion detection, and recordkeeping retention periods, and to whether the policy "
            "assigns a specific accountable role (pharmacist-in-charge vs. 'pharmacy staff').\n"
            "4. Check each requirement against the actual policy text.\n"
            "5. Identify every gap, missing element, vague language, and non-compliant clause.\n"
            "6. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be exact about numbers. Pharmacy compliance is unusually driven by specific retention "
            "periods, inventory intervals, and reporting deadlines, and a wrong figure stated as a "
            "legal requirement is worse than no figure at all. Where the source material does not "
            "state the number, say so rather than supplying one from memory.\n\n"
            "Note that state board of pharmacy rules are frequently STRICTER than federal minimums "
            "(for example on record retention and refill limits); where the policy relies on a "
            "federal minimum, flag that the state requirement must be checked and may control."
        ),
        "regulations": [
            "21 CFR Part 1301 — DEA Registration",
            "21 CFR Part 1304 — DEA Records and Reports (inventory, retention)",
            "21 CFR Part 1306 — DEA Prescription Requirements",
            "Controlled Substances Act (21 U.S.C. §801 et seq.)",
            "USP <795> Non-Sterile Compounding",
            "USP <797> Sterile Compounding",
            "USP <800> Hazardous Drugs Handling",
            "FDCA §503A / §503B — Compounding",
            "Drug Supply Chain Security Act (DSCSA)",
            "42 CFR Part 423 — Medicare Part D (including FWA program requirements)",
            "HIPAA Privacy & Security Rules (45 CFR Parts 160, 164)",
            "State Board of Pharmacy regulations (vary by state, often stricter than federal)",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST also check "
            "all applicable {jurisdiction} Board of Pharmacy regulations, state controlled-substance "
            "scheduling (which can differ from federal schedules), the state Prescription Drug "
            "Monitoring Program (PDMP) query and reporting mandates, state record-retention periods, "
            "pharmacist-to-technician ratio limits, and state compounding rules. State requirements "
            "are frequently stricter than the federal floor — where they conflict, the stricter "
            "controls. Cite state law by code section."
        ),
        "audit_authority": "a DEA inspection, state Board of Pharmacy audit, or Part D plan audit",
    },

    # Added because an industrial safety policy had nowhere to go. A factory's
    # OSHA noise policy had to be filed under Hospitals or Other/General, and
    # under Hospitals the analysis was grounded in HIPAA and OIG nursing-facility
    # guidance and signed off with "consult qualified healthcare compliance
    # counsel".
}

# ── Policy type menus per industry ──
# Each entry: { slug, label, description }

POLICY_TYPES: dict = {
    "pharmacy": [
        {"slug": "controlled_substances",   "label": "Controlled Substances Policy",            "description": "Ordering, storage, access, perpetual inventory, DEA recordkeeping"},
        {"slug": "diversion_prevention",    "label": "Drug Diversion Prevention Policy",        "description": "Detection, monitoring, investigation, DEA Form 106 loss reporting"},
        {"slug": "dispensing_verification", "label": "Dispensing & Verification Policy",         "description": "Prescription validity, corresponding responsibility, final check"},
        {"slug": "sterile_compounding",     "label": "Sterile Compounding Policy (USP 797)",     "description": "Cleanroom, garbing, beyond-use dating, environmental monitoring"},
        {"slug": "nonsterile_compounding",  "label": "Non-Sterile Compounding Policy (USP 795)", "description": "Formulation records, ingredients, beyond-use dating"},
        {"slug": "hazardous_drugs",         "label": "Hazardous Drug Handling Policy (USP 800)", "description": "Receipt, storage, PPE, spill response, staff exposure"},
        {"slug": "dscsa_traceability",      "label": "Drug Supply Chain (DSCSA) Policy",         "description": "Transaction records, verification, suspect and illegitimate product"},
        {"slug": "pdmp_policy",             "label": "PDMP Query Policy",                        "description": "When to query, documentation, red-flag resolution"},
        {"slug": "medication_errors",       "label": "Medication Error & Near-Miss Policy",      "description": "Reporting, root cause analysis, corrective action, patient notification"},
        {"slug": "recalls_returns",         "label": "Recalls, Returns & Disposal Policy",       "description": "Recall handling, reverse distribution, controlled substance destruction"},
        {"slug": "part_d_fwa",              "label": "Medicare Part D FWA Policy",               "description": "Fraud, waste and abuse program, exclusion screening, training"},
        {"slug": "patient_privacy_rx",      "label": "Patient Privacy Policy",                   "description": "HIPAA in the pharmacy setting, counseling privacy, PHI disposal"},
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
        # Conflict of interest and compliance risk assessment are GCPG Elements
        # 2 and 6 and are among the most commonly requested healthcare policies,
        # but neither had an entry -- a hospital asking for either got routed
        # through the generic Code of Conduct type.
        {"slug": "conflict_of_interest_hc",     "label": "Conflict of Interest Policy",        "description": "Disclosure, recusal, gifts, board and physician financial relationships, Stark/AKS overlap"},
        {"slug": "compliance_risk_assessment",  "label": "Compliance Risk Assessment Policy",  "description": "Annual enterprise risk assessment, risk scoring, work plan, auditing & monitoring"},
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
        # Home health loads 45 CFR 164 but had no breach-response policy type,
        # so the one policy that rule most directly requires could not be
        # requested from the menu. Conflict of interest and risk assessment are
        # added for the same reason they are on the hospital menu.
        {"slug": "breach_notification_hh",    "label": "Breach Notification Policy",                  "description": "HIPAA/HITECH breach risk assessment, patient and HHS notification timelines"},
        {"slug": "conflict_of_interest_hh",   "label": "Conflict of Interest Policy",                 "description": "Disclosure, recusal, gifts, referral-source financial relationships"},
        {"slug": "risk_assessment_hh",        "label": "Compliance Risk Assessment Policy",           "description": "Annual risk assessment, auditing and monitoring work plan, corrective action"},
    ],
}

DEFAULT_INDUSTRY = "healthcare"

# The CFR parts every employer is subject to, whatever it does. A hospital
# writing an attendance policy needs FMLA and the ADA; one writing a workplace
# violence policy needs OSHA.
#
# These used to live inside the "Other / General" industry, and every other
# vertical inherited them from there. That made a catch-all category
# load-bearing: removing it would silently have stripped ADA, FMLA and OSHA
# grounding out of the healthcare verticals, and a hospital asking for an
# absenteeism policy would have had nothing real to cite. They belong to no
# single industry, so they are their own list.
BASELINE_EMPLOYMENT_TARGETS = [
    (29, 1630, "29 CFR Part 1630 — ADA Employment Regulations", SourceCategory.federal_regulation),
    (29, 1604, "29 CFR Part 1604 — Sex Discrimination Guidelines", SourceCategory.federal_regulation),
    (29, 825,  "29 CFR Part 825 — FMLA Regulations", SourceCategory.federal_regulation),
    (29, 1910, "29 CFR Part 1910 — OSHA Occupational Safety & Health Standards", SourceCategory.federal_regulation),
    (29, 1601, "29 CFR Part 1601 — EEOC Procedural Regulations (Title VII)", SourceCategory.federal_regulation),
    (29, 541,  "29 CFR Part 541 — FLSA White-Collar Exemptions", SourceCategory.federal_regulation),
    (29, 1635, "29 CFR Part 1635 — GINA (Genetic Information Nondiscrimination)", SourceCategory.federal_regulation),
]


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
