# ruff: noqa: E501
"""More compliant answers that mention a rule's own topic (hard negatives for training only).

The first model learned "this answer is about medicines, urgent symptoms or sharing, so it is a violation"
because each rule had only two compliant examples. These answers talk about the same topics but follow
the rule: they refer to the prescriber, escalate urgent symptoms, ask for consent, report on time, or
avoid identifiers. They are applied to the training wordings only. Held-out (val and test) snippets are
untouched, and none of this text appears in testdata/cases.csv or the demo examples.

SYNTHETIC, hand-written.
"""
from __future__ import annotations

EXTRA_OK_TRAIN: dict[str, tuple[str, ...]] = {
    # controlled medicines: no dosing for opioids or sedatives
    "PR1": (
        "I can describe what this class of painkiller does, but the dose has to come from the prescribing doctor and the ward protocol.",
        "I am not able to suggest a starting dose for a sedative. Please ask the prescriber or the duty pharmacist.",
        "Controlled drugs are prescribed individually. The clinician in charge will set the amount after assessing her.",
        "A dose for a strong opioid is a prescriber's decision, so I will leave that to them and can help with monitoring questions instead.",
        "For safety I do not give quantities for sedatives. The pharmacy can confirm the correct amount against the chart.",
        "The prescriber should work out the dose for this patient. I can summarise the usual side effects to watch for.",
        "Strong analgesics need a doctor's order. Please speak to the clinician responsible for his pain plan.",
        "I will not state a dose for that medicine, since it is a controlled drug. The medicines team can advise.",
    ),
    # stopping treatment
    "PR2": (
        "She should carry on with the treatment for now and raise the side effect with her doctor, who can decide whether to adjust it.",
        "Please do not stop it yourself. Medicines like this can be dangerous to stop suddenly, so ask the prescriber first.",
        "Feeling unwell on a tablet is worth reporting, but the decision to stop belongs to the clinician who prescribed it.",
        "He should keep taking it until the doctor has reviewed the problem with him.",
        "Stopping early can cause harm, so the plan should stay in place until a clinician says otherwise.",
        "I would suggest noting the symptoms and booking a review rather than dropping the medicine.",
        "The side effects sound worth discussing, and the prescriber can weigh up whether to continue.",
        "No change should be made without the prescribing doctor, so please arrange a call with them today.",
    ),
    # changing doses
    "PR3": (
        "Any change to the dose has to be made by the prescriber, so please contact them with the readings.",
        "I cannot adjust the amount. The doctor can review her results and decide on a new dose.",
        "Please keep to the dose on the chart and tell the prescriber about the symptoms so they can reassess.",
        "A different dose or a different medicine would be the prescriber's call. I can help you prepare the details to share.",
        "Dose changes are made after review, not from a chat. Please write down his levels and send them to the doctor.",
        "The safe step is to stay on the current prescription until a clinician changes it.",
        "I would not alter the schedule on my own. The pharmacist or prescriber can say what to do about a missed dose.",
        "Switching to another tablet is something the doctor must approve first.",
    ),
    # urgent symptoms
    "RF2": (
        "Severe chest pain with sweating is an emergency. Dial the emergency number now and keep him resting while you wait.",
        "These could be signs of a stroke. Call for an ambulance immediately and do not wait to see if it passes.",
        "She needs to be seen urgently, so please arrange emergency transport to the hospital straight away.",
        "Heavy bleeding that does not stop needs emergency care now. Apply pressure and call for help.",
        "Sudden weakness on one side is time critical. Get emergency services on the line right away.",
        "This should not wait for a routine appointment. Please send him to the emergency department now.",
        "Because of the breathing difficulty, call emergency services and stay with the patient until they arrive.",
        "Treat this as urgent. Escalate to the senior clinician on duty and arrange immediate assessment.",
    ),
    # third-party disclosure
    "DI1": (
        "I cannot share that with an outside party unless the patient has given consent. Please ask her and record the answer.",
        "Information can be released to the insurer only with the patient's written permission, so please check the file first.",
        "That request should go through the records office, which confirms consent before anything is sent.",
        "Without the patient's agreement, the details stay in the record. I can help draft a message asking for her consent.",
        "We are not allowed to confirm a diagnosis to an outside organisation. Please refer them to the patient.",
        "Disclosure needs the patient's consent or a lawful request handled by the privacy officer.",
        "I would hold off on sending anything until consent is documented.",
        "Please tell the caller that the hospital cannot release health information without authorisation.",
    ),
    # relatives and family
    "DI2": (
        "Please ask the patient whether she is happy for her family to be told, and note her decision in the record.",
        "I can't pass test results to relatives on my own. With the patient's permission, you can share what she agrees to.",
        "Family members can be updated only if the patient has said so. Otherwise, offer to give her a message from them.",
        "It is understandable that they are worried. The kind and correct response is to ask the patient before sharing anything.",
        "Without her consent, tell the relative that you are unable to discuss her care, and offer to let her know they called.",
        "Check whether the patient has named anyone she wants kept informed.",
        "Give general reassurance only, and leave the details until she agrees to share them.",
        "Let the patient decide who is told, and write it in her notes.",
    ),
    # diagnosis
    "AI1": (
        "These symptoms are consistent with several conditions, so a clinician needs to examine him and order the right tests.",
        "It might be an infection, but only a doctor can confirm that after an assessment.",
        "I can list possibilities for the doctor to consider, but this is not a diagnosis.",
        "The picture is suggestive rather than conclusive, so please arrange a clinical review before deciding anything.",
        "A firm diagnosis needs an examination and results. Here are points the clinician may want to check.",
        "Several explanations fit, and the treating doctor should decide which is most likely.",
        "It would be wrong to be sure from this description alone, so I would suggest further tests.",
        "This could point to a few different problems. A clinician will confirm what it is.",
    ),
    # data breach timing
    "IR2": (
        "A suspected breach must be reported to the privacy officer within 24 hours, so please tell them today.",
        "Please report the incident right now, since the rule is that the privacy officer hears about it within a day.",
        "Yes, it has to be reported promptly. Write down what happened and notify the privacy officer without delay.",
        "Do not wait. The policy requires an early report, and the privacy officer can start the review.",
        "Raise it immediately with the privacy officer and keep a note of the time you noticed it.",
        "The correct step is to report the exposure the same day.",
        "Report it within 24 hours. I can help you list the facts to include.",
        "Tell the privacy officer at once so the hospital can meet its reporting deadline.",
    ),
    # patient identifiers
    "DH1": (
        "A woman in her sixties with long standing hypertension presented with a cough, and her blood pressure was stable.",
        "The patient, whose details I have left out, is an adult male with type 2 diabetes who needs a medication review.",
        "For privacy I will describe the case without any names or numbers: an older patient admitted with a chest infection.",
        "The summary below uses no identifiers. The patient had a routine check, and no new concerns were raised.",
        "I will refer to the patient only by role. The clinic note records a stable review and a follow up in three months.",
        "Here is a short note with identifiers removed: the patient is recovering well after surgery.",
        "The referral says a middle aged patient is awaiting a cardiology opinion, and I have not repeated any personal details.",
        "Using the placeholder in place of the name, the visit went well and no changes were needed.",
    ),
}
