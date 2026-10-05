#!/usr/bin/env python3
"""
Generate BeWell XLSForm from i18n strings and the current v2 structure.

Usage:
    python scripts/generate_xlsform.py -o odk-forms/bewell_questionnaire_v2.xlsx
"""

import argparse
import pandas as pd
from pathlib import Path
import sys

# BeWell question groups extracted from dashboard/src/lib/i18n/en.ts
BEWELL_GROUPS = {
    "bw_migration": {
        "label": "Migration background",
        "items": [
            "Birth parent or carer 1 was born outside the UK",
            "Birth parent or carer 2 was born outside the UK",
            "I was born outside the UK",
        ],
    },
    "bw_arrival": {
        "label": "Arrival in the UK",
        "items": ["How old were you when you first arrived in the UK?"],
    },
    "bw_life_sat": {
        "label": "Life satisfaction",
        "items": ["Overall, how satisfied are you with your life nowadays?"],
    },
    "bw_wbeing": {
        "label": "Psychological wellbeing",
        "items": [
            "I've been feeling optimistic about the future",
            "I've been feeling useful",
            "I've been feeling relaxed",
            "I've been dealing with problems well",
            "I've been thinking clearly",
            "I've been feeling close to other people",
            "I've been able to make up my own mind about things",
        ],
    },
    "bw_selfest": {
        "label": "Self-esteem",
        "items": [
            "On the whole, I am satisfied with myself",
            "I feel that I have a number of good qualities",
            "I am able to do things as well as most other people",
            "I am a person of value",
            "I feel good about myself",
        ],
    },
    "bw_emoreg": {
        "label": "Emotion regulation",
        "items": [
            "When I am worried, I keep myself from losing control",
            "When I am worried, I talk to someone until I feel better",
            "When I am worried, I try to calmly settle the problem",
        ],
    },
    "bw_appear": {
        "label": "Appearance happiness",
        "items": ["How happy are you with your appearance?"],
    },
    "bw_stress": {
        "label": "Stress",
        "items": [
            "How often have you felt unable to control important things in your life?",
            "How often have you felt difficulties piling up so high that you could not overcome them?",
        ],
    },
    "bw_coping": {
        "label": "Coping",
        "items": [
            "How often have you felt confident about your ability to handle personal problems?",
            "How often have you felt that things were going your way?",
        ],
    },
    "bw_emodies": {
        "label": "Emotional difficulties",
        "items": [
            "I feel lonely",
            "I am unhappy",
            "Nobody likes me",
            "I cry a lot",
            "I worry when at school",
            "I worry a lot",
            "I have problems sleeping",
            "I wake up in the night",
            "I am shy",
            "I feel scared",
        ],
    },
    "bw_behav": {
        "label": "Behavioural difficulties",
        "items": [
            "I get very angry",
            "I lose my temper",
            "I hit out when I am angry",
            "I am calm",
            "I break things on purpose",
            "I do things to hurt people",
        ],
    },
    "bw_physh": {
        "label": "Physical health",
        "items": ["In general would you say your physical health is:"],
    },
    "bw_sleep": {
        "label": "Sleep adequacy",
        "items": [
            "Is the amount of sleep you get enough to feel awake and concentrate?"
        ],
    },
    "bw_physact": {
        "label": "Physical activity days",
        "items": ["How many days in a usual week are you physically active?"],
    },
    "bw_physdur": {
        "label": "Physical activity duration",
        "items": [
            "On active days, how long on average do you spend being physically active?"
        ],
    },
    "bw_fruitveg": {
        "label": "Fruit and vegetables",
        "items": ["How many portions of fruit or veg do you eat in a typical day?"],
    },
    "bw_unhealthy": {
        "label": "Unhealthy food and drink frequency",
        "items": [
            "Sugary drinks",
            "Diet or sugar-free drinks",
            "Sweets, chocolate and snacks",
            "Take-away and fast food",
        ],
    },
    "bw_freetime": {
        "label": "Free time",
        "items": ["How often can you do things that you like in your free time?"],
    },
    "bw_socmedia": {
        "label": "Social media time",
        "items": ["On a normal weekday, how much time do you spend on social media?"],
    },
    "bw_socmtype": {
        "label": "Social media use type",
        "items": [
            "Active use such as chatting or posting",
            "Passive use such as browsing or scrolling",
        ],
    },
    "bw_volunteer": {
        "label": "Volunteering",
        "items": ["How often do you do volunteer work?"],
    },
    "bw_activ": {
        "label": "Activities when not at school",
        "items": [
            "Listening to music",
            "Going to the cinema or theatre",
            "Watching TV shows or films",
            "Watching live sport",
            "Reading for enjoyment",
            "Youth offer activities such as youth clubs or Scouts",
            "Attending a religious service",
            "Drawing, painting or making things",
            "Computer or console gaming",
            "Sport, exercise or physical activities not at school",
            "Creative hobbies not mentioned above",
        ],
    },
    "bw_schoolconn": {
        "label": "School connection",
        "items": ["I feel that I belong at my school."],
    },
    "bw_attain": {
        "label": "Attainment happiness",
        "items": ["How happy are you with the marks you get in school?"],
    },
    "bw_staffrel": {
        "label": "Relationships with school staff",
        "items": [
            "At school there is an adult who is interested in my schoolwork",
            "At school there is an adult who believes that I will be a success",
            "At school there is an adult who wants me to do my best",
            "At school there is an adult who listens to me when I have something to say",
        ],
    },
    "bw_iso": {
        "label": "School isolation",
        "items": [
            "Think about a typical school week. Are you ever placed in isolation?"
        ],
    },
    "bw_isodays": {
        "label": "School isolation days",
        "items": ["On how many days in a typical week are you placed in isolation?"],
    },
    "bw_isodur": {
        "label": "School isolation duration",
        "items": ["How long does isolation typically last?"],
    },
    "bw_schpress": {
        "label": "School pressure",
        "items": ["How pressured do you feel by the schoolwork you have to do?"],
    },
    "bw_homeenv": {
        "label": "Home environment happiness",
        "items": ["How happy are you with the home that you live in?"],
    },
    "bw_safety": {
        "label": "Safety in local area",
        "items": ["How safe do you feel when in your local area?"],
    },
    "bw_localenv": {
        "label": "Local environment quality",
        "items": [
            "People around here support each other with their wellbeing",
            "You can trust people around here",
            "I could ask for help or a favour from neighbours",
            "There are good places to spend your free time",
        ],
    },
    "bw_beinheard": {
        "label": "Being heard outside school and home",
        "items": ["Away from school and home, there is an adult who listens to me."],
    },
    "bw_foodsec": {
        "label": "Food security",
        "items": [
            "The food that we bought just did not last. How often was this true?"
        ],
    },
    "bw_material": {
        "label": "Material wellbeing",
        "items": [
            "How happy are you with the things that you have, like money and things you own?"
        ],
    },
    "bw_future": {
        "label": "Future readiness",
        "items": [
            "I have hope and feel optimistic about my future",
            "I feel that my generation will have a better life than my parents' generation",
            "I am generally confident in my own skills and abilities",
            "I usually cope well with most unexpected problems",
            "When I finish education, I will have the skills I need to be prepared for life",
            "If I do well with education, I will have the same chances as anyone else of getting a job",
            "I feel in control about future education, training and job prospects",
        ],
    },
    "bw_careersed": {
        "label": "Careers education received",
        "items": ["How many types of careers education have you received at school?"],
    },
    "bw_careershlp": {
        "label": "Careers education helpfulness",
        "items": [
            "How helpful has the careers education you have received at school been?"
        ],
    },
    "bw_plans": {
        "label": "Post-Year-11 plans",
        "items": [
            "Might do this after Year 11: school sixth form",
            "Might do this after Year 11: further education or sixth-form college",
            "Might do this after Year 11: UTC or Studio School",
            "Might do this after Year 11: apprenticeship or traineeship",
            "Might do this after Year 11: supported internship",
            "Might do this after Year 11: T level",
            "Might do this after Year 11: get a job",
            "Might do this after Year 11: start a business",
        ],
    },
    "bw_gmacs": {
        "label": "GMACS",
        "items": [
            "Do you know what GMACS is?",
            "Have you used the GMACS website in the last 12 months?",
        ],
    },
    "bw_parentsrel": {
        "label": "Relationships with parents or carers",
        "items": [
            "At home there is an adult who is interested in my schoolwork",
            "At home there is an adult who believes that I will be a success",
            "At home there is an adult who wants me to do my best",
            "At home there is an adult who listens to me when I have something to say",
        ],
    },
    "bw_friends": {
        "label": "Friendships and social support",
        "items": [
            "I get along with people around me",
            "People like to spend time with me",
            "I feel supported by my friends",
            "My friends care about me when times are hard",
        ],
    },
    "bw_lonely": {
        "label": "Loneliness",
        "items": ["How often do you feel lonely?"],
    },
    "bw_discrim": {
        "label": "Discrimination experiences",
        "items": [
            "People make me feel bad because of my race or skin colour",
            "People make me feel bad because of my gender",
            "People make me feel bad because of my sexual orientation",
            "People make me feel bad because of my disability",
            "People make me feel bad because of my religion or faith",
        ],
    },
    "bw_discloc": {
        "label": "Where discrimination happened",
        "items": [
            "At home",
            "Walking to or from school",
            "On public transport",
            "At school",
            "In my local area",
            "Online",
            "Somewhere else",
        ],
    },
    "bw_bullying": {
        "label": "Bullying",
        "items": ["Physical bullying", "Social bullying", "Cyber-bullying"],
    },
    "bw_support": {
        "label": "Access to wellbeing support",
        "items": [
            "I have a place to seek support for worries or mental health concerns."
        ],
    },
    "bw_mhcontact": {
        "label": "Mental health contact",
        "items": [
            "Someone in your family",
            "A close friend",
            "A trusted adult who is not at school and not in your family",
            "A teacher",
            "A school mental health worker",
            "Online help such as websites, social media or self-help groups",
        ],
    },
    "bw_kooth": {
        "label": "Kooth",
        "items": ["Have you heard of or used Kooth, the online wellbeing service?"],
    },
}

# Demographic choice lists
CHOICES = {
    "sex": [
        {"name": "M", "label": "Male"},
        {"name": "F", "label": "Female"},
        {"name": "I", "label": "Intersex"},
    ],
    "ethnicity": [
        {"name": "White_British", "label": "White British"},
        {"name": "Asian", "label": "Asian"},
        {"name": "Black", "label": "Black"},
        {"name": "Mixed", "label": "Mixed"},
        {"name": "Other", "label": "Other"},
    ],
    "sexual_orientation": [
        {"name": "Heterosexual", "label": "Heterosexual"},
        {"name": "Gay_or_lesbian", "label": "Gay or lesbian"},
        {"name": "Bisexual", "label": "Bisexual"},
        {"name": "Other", "label": "Other"},
    ],
    "gender_identity": [
        {"name": "Cisgender", "label": "Cisgender"},
        {"name": "Transgender", "label": "Transgender"},
        {"name": "Non_binary", "label": "Non-binary"},
        {"name": "Other", "label": "Other"},
    ],
}


def generate_xlsform(output_path: Path):
    """Generate BeWell questionnaire XLSForm."""

    # Survey sheet
    survey_rows = []

    # Shared metadata fields
    survey_rows.append(
        {
            "type": "text",
            "name": "uid",
            "label": "Unique ID",
            "constraint": "",
            "required": "no",
        }
    )
    survey_rows.append(
        {
            "type": "text",
            "name": "school",
            "label": "School",
            "constraint": "",
            "required": "no",
        }
    )

    # BeWell questions
    for prefix, group in BEWELL_GROUPS.items():
        for i, item_label in enumerate(group["items"], start=1):
            field_name = f"{prefix}_{i}"
            full_label = f"{group['label']}: {item_label}"

            survey_rows.append(
                {
                    "type": "integer",
                    "name": field_name,
                    "label": full_label,
                    "constraint": ". >= 0 and . <= 5",
                    "required": "no",
                }
            )

    survey_df = pd.DataFrame(survey_rows)

    # Choices sheet
    choices_rows = []
    for list_name, choices in CHOICES.items():
        for choice in choices:
            choices_rows.append(
                {
                    "list_name": list_name,
                    "name": choice["name"],
                    "label": choice["label"],
                }
            )

    choices_df = pd.DataFrame(choices_rows)

    # Settings sheet
    settings_df = pd.DataFrame(
        [
            {
                "form_id": "bewell_questionnaire",
                "version": "2",
                "form_title": "BeWell Questionnaire (v2)",
            }
        ]
    )

    # Write to Excel
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        survey_df.to_excel(writer, sheet_name="survey", index=False)
        choices_df.to_excel(writer, sheet_name="choices", index=False)
        settings_df.to_excel(writer, sheet_name="settings", index=False)

    print(f"✅ XLSForm generated: {output_path}")
    print(f"   - {len(survey_rows)} survey fields")
    print(f"   - {len(choices_rows)} choice options")
    print(f"   - {len(BEWELL_GROUPS)} BeWell question groups")


def main():
    parser = argparse.ArgumentParser(description="Generate BeWell XLSForm")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("odk-forms/bewell_questionnaire_v2.xlsx"),
        help="Output XLSForm path (default: odk-forms/bewell_questionnaire_v2.xlsx)",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    generate_xlsform(args.output)


if __name__ == "__main__":
    main()
