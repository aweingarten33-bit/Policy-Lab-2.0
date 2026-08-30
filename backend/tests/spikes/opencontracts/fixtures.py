"""eCFR Versioner XML payloads for the compatibility spike.

FIXTURE TEXT. www.ecfr.gov is unreachable from this sandbox (the outbound proxy
refuses the CONNECT with 403), so these payloads stand in for the live
response. They are written in the eCFR Versioner XML shape OpenContracts'
parser expects -- ``DIV8 TYPE="SECTION" N="..."`` with a ``HEAD`` and ``P``
children -- and their wording follows 45 CFR § 164.404 closely, but they are
not a verified copy of the regulation and must not be read as one. Nothing in
the spike depends on the wording being exact: what is under test is whether the
verification pipeline reaches the same verdicts against an OpenContracts
authority as it does against Chroma.

The variants exist to drive one fail-closed path each:

  ``SECTION_164_404``          the current provision (result A)
  ``SECTION_164_404_AMENDED``  the same provision, reworded (result E)
  ``SECTION_164_530``          a second current provision, for the
                               proposed / superseded / expired standing cases
"""

from __future__ import annotations


def _section_xml(number: str, heading: str, paragraphs: list[str]) -> bytes:
    body = "".join(f"<P>{p}</P>" for p in paragraphs)
    return (
        f'<DIV5 TYPE="PART" N="164">'
        f'<DIV8 TYPE="SECTION" N="{number}">'
        f"<HEAD>§ {number} {heading}</HEAD>{body}"
        f"</DIV8></DIV5>"
    ).encode("utf-8")


SECTION_164_404 = _section_xml(
    "164.404",
    "Notification to individuals.",
    [
        "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
        "of a breach of unsecured protected health information, notify each individual "
        "whose unsecured protected health information has been, or is reasonably believed "
        "by the covered entity to have been, accessed, acquired, used, or disclosed as a "
        "result of such breach.",
        "(2) Breaches treated as discovered. A breach shall be treated as discovered by a "
        "covered entity as of the first day on which such breach is known to the covered "
        "entity, or, by exercising reasonable diligence, would have been known.",
        "(b) Implementation specification: Timeliness of notification. Except as provided "
        "in § 164.412, a covered entity shall provide the notification required by "
        "paragraph (a) of this section without unreasonable delay and in no case later "
        "than 60 calendar days after discovery of a breach.",
        "(c) Implementation specifications: Content of notification. The notification "
        "shall include, to the extent possible, a brief description of what happened, a "
        "description of the types of unsecured protected health information involved, any "
        "steps individuals should take to protect themselves from potential harm, and "
        "contact procedures for individuals to ask questions or learn additional "
        "information.",
        "(d) Implementation specifications: Methods of individual notification. The "
        "notification shall be provided in written form by first-class mail to the "
        "individual at the last known address of the individual, or, if the individual "
        "agrees to electronic notice, by electronic mail.",
    ],
)

# The same section after an amendment: the deadline moves, and the language of
# paragraph (a) is rewritten. Used only to prove that a remembered verification
# is not reused once the authority text changes underneath it.
SECTION_164_404_AMENDED = _section_xml(
    "164.404",
    "Notification to individuals.",
    [
        "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
        "of a breach of unsecured protected health information, notify each affected "
        "individual and the individual's personal representative.",
        "(b) Implementation specification: Timeliness of notification. A covered entity "
        "shall provide the notification required by paragraph (a) of this section without "
        "unreasonable delay and in no case later than 30 calendar days after discovery of "
        "a breach.",
        "(c) Implementation specifications: Content of notification. The notification "
        "shall include a brief description of what happened and the date of the breach.",
    ],
)

SECTION_164_530 = _section_xml(
    "164.530",
    "Administrative requirements.",
    [
        "(b) Standard: Training—(1) A covered entity must train all members of its "
        "workforce on the policies and procedures with respect to protected health "
        "information required by this subpart.",
        "(2) Implementation specifications: Training. A covered entity must provide "
        "training to each member of the workforce by no later than the compliance date "
        "for the covered entity, and thereafter to each new member of the workforce "
        "within a reasonable period of time after the person joins the workforce.",
        "(e) Standard: Sanctions. A covered entity must have and apply appropriate "
        "sanctions against members of its workforce who fail to comply with the privacy "
        "policies and procedures of the covered entity or the requirements of this "
        "subpart.",
    ],
)
