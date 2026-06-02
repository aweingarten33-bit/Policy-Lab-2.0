"""
Industry Configuration — Defines all supported compliance verticals.

Each industry entry specifies:
  - Display metadata (name, icon, description)
  - eCFR regulatory targets (title, part, label, category)
  - Federal Register agency filters for live research
  - LLM persona for the system prompt
  - Key regulations to check
  - State-specific addendum template
  - Live research curated sources to use
"""

from app.services.retrieval.models import SourceCategory

INDUSTRIES: dict = {
    "healthcare": {
        "name": "Healthcare",
        "icon": "🏥",
        "description": "Hospitals, clinics, health plans, covered entities, business associates",
        "ecfr_targets": [
            (45, 160, "45 CFR Part 160 — General HIPAA Provisions", SourceCategory.federal_regulation),
            (45, 164, "45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification", SourceCategory.federal_regulation),
            (42, 2,   "42 CFR Part 2 — Substance Abuse Confidentiality", SourceCategory.federal_regulation),
            (42, 482, "42 CFR Part 482 — Conditions of Participation (Hospitals)", SourceCategory.federal_regulation),
        ],
        "fr_agencies": [
            "health-and-human-services-department",
            "centers-for-medicare-medicaid-services",
            "office-for-civil-rights-hhs",
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

    "education": {
        "name": "Education / Childcare",
        "icon": "🏫",
        "description": "Private schools, preschools, childcare centers, daycares, educational franchises (e.g., Goddard School)",
        "ecfr_targets": [
            (34, 99,  "34 CFR Part 99 — FERPA (Student Records Privacy)", SourceCategory.federal_regulation),
            (34, 104, "34 CFR Part 104 — Section 504 / Rehab Act (Disability)", SourceCategory.federal_regulation),
            (34, 106, "34 CFR Part 106 — Title IX (Sex Discrimination)", SourceCategory.federal_regulation),
            (34, 300, "34 CFR Part 300 — IDEA (Special Education)", SourceCategory.federal_regulation),
        ],
        "fr_agencies": [
            "education-department",
            "health-and-human-services-department",
        ],
        "live_research_sources": [
            "education_dept", "federal_register"
        ],
        "persona": (
            "You are the most senior education and childcare compliance expert in the United States, specializing in "
            "private preschools, daycare centers, childcare franchises, and independent K-12 schools. You advise school "
            "directors, franchise owners, and compliance officers navigating the intersection of federal education law, "
            "state childcare licensing, and local health and safety codes.\n\n"
            "A user will provide a school policy, childcare center procedure, or compliance document. Your job:\n\n"
            "1. Read the policy carefully and identify the exact policy type.\n"
            "2. Identify EVERY federal, state, and local regulation that applies — do not limit yourself. Key frameworks: "
            "FERPA (student records), Title IX (sex discrimination), Section 504/ADA (disability accommodations), IDEA "
            "(special education), CAPTA (child abuse), mandatory reporting laws, state childcare licensing regulations "
            "(e.g., NY OCFS 18 NYCRR Part 418, NYC DOHMH Article 47), CDC/AAP Caring for Our Children health and safety "
            "guidelines, OSHA Bloodborne Pathogens, staff background check requirements (SCR, NYSOPWDD), CACFP "
            "requirements where applicable, and for franchise schools (e.g., Goddard School): franchisor policy standards "
            "and NY State education department requirements.\n"
            "3. For NAEYC-accredited or NAEYC-seeking programs: evaluate against all 10 NAEYC Early Learning Program "
            "Standards — (1) Relationships, (2) Curriculum, (3) Teaching, (4) Assessment of Child Progress, "
            "(5) Health, (6) Staff Competencies & Dispositions, (7) Families, (8) Community Relationships, "
            "(9) Physical Environment, (10) Leadership & Management — and flag which NAEYC standard tier "
            "(Recognition, Accreditation, or Accreditation+) applies under the 2024–2025 redesigned system. "
            "Also consider state QRIS star ratings (e.g., NY QUALITYstarsNY) where relevant.\n"
            "4. Check each regulation against the actual policy text.\n"
            "5. Identify every gap, missing element, vague language, or non-compliant clause.\n"
            "6. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be thorough. A school director and franchise owner need to know exactly what would fail a state licensing "
            "inspection, a Title IX OCR complaint review, a NAEYC accreditation site visit, or a parent lawsuit today.\n\n"
            "Flag any 2024–2026 updates to FERPA guidance, Title IX regulations (including the 2024 Title IX rule "
            "changes), state childcare licensing changes, NAEYC accreditation system redesign, or CDC/AAP updates."
        ),
        "regulations": [
            "FERPA — Family Educational Rights and Privacy Act (34 CFR Part 99)",
            "Title IX — Sex Discrimination in Education (34 CFR Part 106, 2024 Rule)",
            "Section 504 — Rehabilitation Act / ADA (34 CFR Part 104)",
            "IDEA — Individuals with Disabilities Education Act (34 CFR Part 300)",
            "CAPTA — Child Abuse Prevention and Treatment Act",
            "Mandatory Reporting Laws (state-specific — NY Social Services Law §413)",
            "NY OCFS Childcare Center Regulations (18 NYCRR Part 418)",
            "NYC DOHMH Article 47 (NYC Childcare Center Regulations)",
            "ADA Title III (Public Accommodations)",
            "CDC/AAP Caring for Our Children Health & Safety Standards (4th Ed.)",
            "NAEYC 10 Early Learning Program Standards (2024–2025 Accreditation System)",
            "CACFP — Child and Adult Care Food Program (7 CFR Part 226)",
            "OSHA Bloodborne Pathogens (29 CFR 1910.1030)",
            "NY Staff Background Check — SCR & Statewide Central Register",
            "Franchisor Standards (Goddard Systems Inc. / applicable franchise agreement)",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST check all applicable "
            "{jurisdiction} state childcare licensing regulations, mandatory reporting statutes, background check "
            "requirements, health and safety codes, and any relevant state education department guidance. "
            "For NY: cite NY OCFS (18 NYCRR), NYC DOHMH Article 47, NY Social Services Law §413, and "
            "NY Education Law as applicable. Cite all state law by code section."
        ),
        "audit_authority": "state licensing inspection, OCR complaint review, or child protective services investigation",
    },

    "hoa": {
        "name": "HOA / 55+ Communities",
        "icon": "🏘️",
        "description": "Homeowners associations, condo associations, age-restricted 55+ retirement communities (Long Island, NY)",
        "ecfr_targets": [
            (24, 100, "24 CFR Part 100 — Fair Housing Act Regulations", SourceCategory.federal_regulation),
            (24, 107, "24 CFR Part 107 — Non-Discrimination in HUD Programs", SourceCategory.federal_regulation),
            (24, 966, "24 CFR Part 966 — Public Housing Lease & Grievance Procedures", SourceCategory.federal_regulation),
        ],
        "fr_agencies": [
            "housing-and-urban-development-department",
        ],
        "live_research_sources": [
            "hud_guidance", "federal_register"
        ],
        "persona": (
            "You are the most senior Fair Housing, HOA, and age-restricted community compliance expert in the United States, "
            "with deep expertise in New York State HOA and condominium law. You advise HOA boards, property managers, "
            "and community association attorneys on Fair Housing Act compliance, 55+ age-restriction qualification, "
            "and state-specific condominium and HOA regulations.\n\n"
            "A user will provide an HOA policy, community rules, governing document, or procedure. Your job:\n\n"
            "1. Read the policy carefully and identify the exact document type (Rules & Regulations, Declaration, "
            "Bylaws, House Rules, Age Verification Policy, Board Resolution, etc.).\n"
            "2. Identify EVERY federal, state, and local law that applies. Key frameworks: Fair Housing Act (42 U.S.C. "
            "§3604, §3607), Housing for Older Persons Act (HOPA) and the 55+ exemption requirements (24 CFR §100.304–"
            "100.310), ADA (common areas and amenities), NY Real Property Law (RPL), NY Common Interest Ownership Act, "
            "NY Attorney General HOA regulations, and applicable local codes for Long Island communities (Nassau and "
            "Suffolk counties).\n"
            "3. For 55+ communities: verify the three HOPA prongs — (1) at least 80% of occupied units have one "
            "resident age 55+, (2) published and adhered-to policies demonstrating intent to be 55+ housing, "
            "(3) HUD age verification survey compliance every two years.\n"
            "4. Apply CAI (Community Associations Institute) governance best practices: check that all board resolutions "
            "and policies include the four required CAI elements — (a) Authority (citing specific articles from governing "
            "documents), (b) Rationale (business reason for the rule), (c) Application (who is affected, duration, and "
            "penalties), (d) The Resolution (the actual rules being adopted). Policies must include: Purpose, Scope, "
            "Procedures, Consequences, and Review Mechanisms. Enforcement processes must provide adequate due process — "
            "opportunity to appear before a hearing panel after a violation notice is issued and not cured.\n"
            "5. Check each regulation against the actual policy text.\n"
            "6. Identify every gap, missing element, vague language, or non-compliant clause — including policies that "
            "'call out specific groups of people' in violation of Fair Housing Act requirements.\n"
            "7. For every gap, write the exact policy language that should replace or be added.\n\n"
            "Be specific. An HOA board needs to know exactly what would expose them to a HUD complaint, "
            "a Fair Housing lawsuit, or a NY AG investigation today.\n\n"
            "Flag any 2024–2026 HUD guidance updates, Fair Housing enforcement actions, CAI governance changes, "
            "or NY state HOA/condominium legislative changes."
        ),
        "regulations": [
            "Fair Housing Act (42 U.S.C. §3604, §3607)",
            "Housing for Older Persons Act (HOPA) — 42 U.S.C. §3607(b)",
            "24 CFR Part 100 — Fair Housing Act Regulations",
            "24 CFR §100.304–100.310 — 55+ Housing Exemption Requirements",
            "HUD Handbook 8024.1 — Fair Housing Act Guidance",
            "ADA Title III — Common Areas and Amenities",
            "NY Real Property Law (RPL) §§ 339-i to 339-kk (Condominiums)",
            "NY Common Interest Ownership Act (CIOA)",
            "NY Attorney General Offering Plan Requirements",
            "NY AG HOA Regulations",
            "Nassau / Suffolk County Fair Housing Ordinances",
            "CAI Governance Standards — Board Resolution Format (Authority / Rationale / Application / Resolution)",
        ],
        "state_addendum": (
            "IMPORTANT: The user has specified jurisdiction \"{jurisdiction}\". You MUST check all applicable "
            "{jurisdiction} state HOA and condominium laws, Fair Housing enforcement, and local county ordinances. "
            "For NY communities: cite NY Real Property Law, NY CIOA, NY AG regulations, and any applicable "
            "Nassau/Suffolk county fair housing ordinances. For 55+ communities: verify all three HOPA qualification "
            "prongs are addressed. Cite all state law by code section."
        ),
        "audit_authority": "HUD complaint, Fair Housing lawsuit, or NY AG investigation",
    },
    "other": {
        "name": "Other / General",
        "icon": "📋",
        "description": "Any organization — general best practices and inferred regulations from your policy content",
        "ecfr_targets": [
            (29, 1630, "29 CFR Part 1630 — ADA Employment Regulations", SourceCategory.federal_regulation),
            (29, 1604, "29 CFR Part 1604 — Sex Discrimination Guidelines", SourceCategory.federal_regulation),
            (29, 825,  "29 CFR Part 825 — FMLA Regulations", SourceCategory.federal_regulation),
        ],
        "fr_agencies": ["federal-register", "equal-employment-opportunity-commission", "labor-department"],
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
    "education": [
        {"slug": "code_of_conduct",             "label": "Student & Family Code of Conduct",   "description": "Behavior expectations, discipline, family rights"},
        {"slug": "child_protection",            "label": "Child Protection & Mandatory Reporting Policy", "description": "Abuse/neglect identification, NY mandated reporter obligations"},
        {"slug": "health_illness",              "label": "Health & Illness Exclusion Policy",  "description": "Illness exclusion criteria, return-to-school, DOHMH requirements"},
        {"slug": "ferpa_privacy",               "label": "Student Records Privacy Policy (FERPA)", "description": "Record access, consent, disclosure rules under 34 CFR Part 99"},
        {"slug": "emergency_procedures",        "label": "Emergency & Evacuation Procedures",  "description": "Fire, lockdown, shelter-in-place, reunification plan"},
        {"slug": "allergy_anaphylaxis",         "label": "Allergy & Anaphylaxis Management Policy", "description": "EpiPen, emergency action plans, staff training, ADA accommodations"},
        {"slug": "incident_injury_reporting",   "label": "Incident & Injury Reporting Policy", "description": "Documentation, parent notification, OCFS/DOHMH reporting"},
        {"slug": "staff_supervision",           "label": "Staff Background Check & Supervision Policy", "description": "Fingerprinting, references, ratios, supervision requirements"},
        {"slug": "medication_administration",   "label": "Medication Administration Policy",   "description": "Consent, storage, administration procedures, recordkeeping"},
        {"slug": "visitor_volunteer",           "label": "Visitor & Volunteer Policy",         "description": "Sign-in, background checks, supervision of non-staff adults"},
        {"slug": "media_photography",           "label": "Media, Photography & Social Media Policy", "description": "Consent, FERPA implications, staff social media conduct"},
        {"slug": "ada_accommodations",          "label": "ADA / Section 504 Accommodations Policy", "description": "Disability accommodations, individualized plans, non-discrimination"},
        {"slug": "grievance_procedure",         "label": "Grievance & Complaint Procedure",    "description": "Parent/family complaint process, anti-retaliation, Title IX grievance"},
        {"slug": "nap_rest_time",               "label": "Napping & Rest Time Policy",         "description": "OCFS-required rest provisions, safe sleep (infants), supervision"},
        {"slug": "nutrition_food",              "label": "Nutrition & Food Safety Policy",     "description": "Meal standards, food allergies, CACFP requirements if applicable"},
    ],
    "hoa": [
        {"slug": "community_rules",             "label": "Community Rules & Regulations",      "description": "General conduct, common areas, noise, parking, pets"},
        {"slug": "age_verification",            "label": "55+ Age Verification Policy",        "description": "HOPA three-prong test compliance, survey, recordkeeping"},
        {"slug": "fair_housing_policy",         "label": "Fair Housing Compliance Policy",     "description": "Non-discrimination in sales, rentals, rules enforcement"},
        {"slug": "common_area_use",             "label": "Common Area Use Policy",             "description": "Pool, gym, clubhouse rules, ADA access, reservations"},
        {"slug": "architectural_review",        "label": "Architectural Review Policy",        "description": "Modification requests, approval process, design standards"},
        {"slug": "pet_policy",                  "label": "Pet Policy",                         "description": "Permitted animals, leash rules, ADA service animal compliance"},
        {"slug": "assessment_collection",       "label": "Assessment & Collection Policy",     "description": "Dues, late fees, lien procedures, NY RPL compliance"},
        {"slug": "grievance_dispute",           "label": "Grievance & Dispute Resolution Policy", "description": "Resident complaint process, hearing procedures, NY requirements"},
        {"slug": "board_meetings",              "label": "Board Meeting & Voting Policy",      "description": "Notice requirements, quorum, proxy voting, NY CIOA compliance"},
        {"slug": "rental_restrictions",         "label": "Rental Restriction Policy",          "description": "Leasing rules, tenant screening, Fair Housing compliance"},
        {"slug": "maintenance_repair",          "label": "Maintenance & Repair Policy",        "description": "Responsibility matrix, repair requests, contractor access"},
    ],
}

DEFAULT_INDUSTRY = "healthcare"


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
