type QuestionGroup = {
  label: string;
  items: string[];
};

const beWellQuestionGroups: Record<string, QuestionGroup> = {
  bw_migration: {
    label: "Migration background",
    items: [
      "Birth parent or carer 1 was born outside the UK",
      "Birth parent or carer 2 was born outside the UK",
      "I was born outside the UK",
    ],
  },
  bw_arrival: {
    label: "Arrival in the UK",
    items: ["How old were you when you first arrived in the UK?"],
  },
  bw_life_sat: {
    label: "Life satisfaction",
    items: ["Overall, how satisfied are you with your life nowadays?"],
  },
  bw_wbeing: {
    label: "Psychological wellbeing",
    items: [
      "I've been feeling optimistic about the future",
      "I've been feeling useful",
      "I've been feeling relaxed",
      "I've been dealing with problems well",
      "I've been thinking clearly",
      "I've been feeling close to other people",
      "I've been able to make up my own mind about things",
    ],
  },
  bw_selfest: {
    label: "Self-esteem",
    items: [
      "On the whole, I am satisfied with myself",
      "I feel that I have a number of good qualities",
      "I am able to do things as well as most other people",
      "I am a person of value",
      "I feel good about myself",
    ],
  },
  bw_emoreg: {
    label: "Emotion regulation",
    items: [
      "When I am worried, I keep myself from losing control",
      "When I am worried, I talk to someone until I feel better",
      "When I am worried, I try to calmly settle the problem",
    ],
  },
  bw_appear: {
    label: "Appearance happiness",
    items: ["How happy are you with your appearance?"],
  },
  bw_stress: {
    label: "Stress",
    items: [
      "How often have you felt unable to control important things in your life?",
      "How often have you felt difficulties piling up so high that you could not overcome them?",
    ],
  },
  bw_coping: {
    label: "Coping",
    items: [
      "How often have you felt confident about your ability to handle personal problems?",
      "How often have you felt that things were going your way?",
    ],
  },
  bw_emodies: {
    label: "Emotional difficulties",
    items: [
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
  bw_behav: {
    label: "Behavioural difficulties",
    items: [
      "I get very angry",
      "I lose my temper",
      "I hit out when I am angry",
      "I am calm",
      "I break things on purpose",
      "I do things to hurt people",
    ],
  },
  bw_physh: {
    label: "Physical health",
    items: ["In general would you say your physical health is:"],
  },
  bw_sleep: {
    label: "Sleep adequacy",
    items: [
      "Is the amount of sleep you get enough to feel awake and concentrate?",
    ],
  },
  bw_physact: {
    label: "Physical activity days",
    items: ["How many days in a usual week are you physically active?"],
  },
  bw_physdur: {
    label: "Physical activity duration",
    items: [
      "On active days, how long on average do you spend being physically active?",
    ],
  },
  bw_fruitveg: {
    label: "Fruit and vegetables",
    items: ["How many portions of fruit or veg do you eat in a typical day?"],
  },
  bw_unhealthy: {
    label: "Unhealthy food and drink frequency",
    items: [
      "Sugary drinks",
      "Diet or sugar-free drinks",
      "Sweets, chocolate and snacks",
      "Take-away and fast food",
    ],
  },
  bw_freetime: {
    label: "Free time",
    items: ["How often can you do things that you like in your free time?"],
  },
  bw_socmedia: {
    label: "Social media time",
    items: ["On a normal weekday, how much time do you spend on social media?"],
  },
  bw_socmtype: {
    label: "Social media use type",
    items: [
      "Active use such as chatting or posting",
      "Passive use such as browsing or scrolling",
    ],
  },
  bw_volunteer: {
    label: "Volunteering",
    items: ["How often do you do volunteer work?"],
  },
  bw_activ: {
    label: "Activities when not at school",
    items: [
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
  bw_schoolconn: {
    label: "School connection",
    items: ["I feel that I belong at my school."],
  },
  bw_attain: {
    label: "Attainment happiness",
    items: ["How happy are you with the marks you get in school?"],
  },
  bw_staffrel: {
    label: "Relationships with school staff",
    items: [
      "At school there is an adult who is interested in my schoolwork",
      "At school there is an adult who believes that I will be a success",
      "At school there is an adult who wants me to do my best",
      "At school there is an adult who listens to me when I have something to say",
    ],
  },
  bw_iso: {
    label: "School isolation",
    items: [
      "Think about a typical school week. Are you ever placed in isolation?",
    ],
  },
  bw_isodays: {
    label: "School isolation days",
    items: ["On how many days in a typical week are you placed in isolation?"],
  },
  bw_isodur: {
    label: "School isolation duration",
    items: ["How long does isolation typically last?"],
  },
  bw_schpress: {
    label: "School pressure",
    items: ["How pressured do you feel by the schoolwork you have to do?"],
  },
  bw_homeenv: {
    label: "Home environment happiness",
    items: ["How happy are you with the home that you live in?"],
  },
  bw_safety: {
    label: "Safety in local area",
    items: ["How safe do you feel when in your local area?"],
  },
  bw_localenv: {
    label: "Local environment quality",
    items: [
      "People around here support each other with their wellbeing",
      "You can trust people around here",
      "I could ask for help or a favour from neighbours",
      "There are good places to spend your free time",
    ],
  },
  bw_beinheard: {
    label: "Being heard outside school and home",
    items: ["Away from school and home, there is an adult who listens to me."],
  },
  bw_foodsec: {
    label: "Food security",
    items: [
      "The food that we bought just did not last. How often was this true?",
    ],
  },
  bw_material: {
    label: "Material wellbeing",
    items: [
      "How happy are you with the things that you have, like money and things you own?",
    ],
  },
  bw_future: {
    label: "Future readiness",
    items: [
      "I have hope and feel optimistic about my future",
      "I feel that my generation will have a better life than my parents' generation",
      "I am generally confident in my own skills and abilities",
      "I usually cope well with most unexpected problems",
      "When I finish education, I will have the skills I need to be prepared for life",
      "If I do well with education, I will have the same chances as anyone else of getting a job",
      "I feel in control about future education, training and job prospects",
    ],
  },
  bw_careersed: {
    label: "Careers education received",
    items: ["How many types of careers education have you received at school?"],
  },
  bw_careershlp: {
    label: "Careers education helpfulness",
    items: [
      "How helpful has the careers education you have received at school been?",
    ],
  },
  bw_plans: {
    label: "Post-Year-11 plans",
    items: [
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
  bw_gmacs: {
    label: "GMACS",
    items: [
      "Do you know what GMACS is?",
      "Have you used the GMACS website in the last 12 months?",
    ],
  },
  bw_parentsrel: {
    label: "Relationships with parents or carers",
    items: [
      "At home there is an adult who is interested in my schoolwork",
      "At home there is an adult who believes that I will be a success",
      "At home there is an adult who wants me to do my best",
      "At home there is an adult who listens to me when I have something to say",
    ],
  },
  bw_friends: {
    label: "Friendships and social support",
    items: [
      "I get along with people around me",
      "People like to spend time with me",
      "I feel supported by my friends",
      "My friends care about me when times are hard",
    ],
  },
  bw_lonely: {
    label: "Loneliness",
    items: ["How often do you feel lonely?"],
  },
  bw_discrim: {
    label: "Discrimination experiences",
    items: [
      "People make me feel bad because of my race or skin colour",
      "People make me feel bad because of my gender",
      "People make me feel bad because of my sexual orientation",
      "People make me feel bad because of my disability",
      "People make me feel bad because of my religion or faith",
    ],
  },
  bw_discloc: {
    label: "Where discrimination happened",
    items: [
      "At home",
      "Walking to or from school",
      "On public transport",
      "At school",
      "In my local area",
      "Online",
      "Somewhere else",
    ],
  },
  bw_bullying: {
    label: "Bullying",
    items: ["Physical bullying", "Social bullying", "Cyber-bullying"],
  },
  bw_support: {
    label: "Access to wellbeing support",
    items: [
      "I have a place to seek support for worries or mental health concerns.",
    ],
  },
  bw_mhcontact: {
    label: "Mental health contact",
    items: [
      "Someone in your family",
      "A close friend",
      "A trusted adult who is not at school and not in your family",
      "A teacher",
      "A school mental health worker",
      "Online help such as websites, social media or self-help groups",
    ],
  },
  bw_kooth: {
    label: "Kooth",
    items: ["Have you heard of or used Kooth, the online wellbeing service?"],
  },
};

function buildBeeWellColumnLabels(
  groups: Record<string, QuestionGroup>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(groups).flatMap(([prefix, group]) =>
      group.items.map((item, index) => [
        `${prefix}_${index + 1}`,
        `${group.label}: ${item}`,
      ]),
    ),
  );
}

export const en = {
  nav: {
    home: "Home",
    explore: "Explore",
    query: "Query",
    admin: "Admin",
    dashboard: "Dashboard",
    signIn: "Sign In",
    signOut: "Sign out",
    adminBadge: "admin",
    api: "API",
    apiDown: "API down",
    online: "Online",
    offline: "Offline",
    checking: "checking",
    apiStatus: "API status: {status}",
  },
  chart: {
    showTable: "Show Table",
    hideTable: "Hide Table",
    downloadCsv: "Download CSV",
    noData: "No data to display",
    suppressionNotice:
      "Some values are suppressed to protect student privacy because the cell counts are small.",
    count: "Count",
    mean: "Mean",
    neighbour: "Neighbour",
  },
  table: {
    noData: "No data available.",
    sortBy: "Sort by {label}",
  },
  dashboard: {
    title: "Dashboard",
    subtitle: "Overview of GLOW longitudinal questionnaire data.",
    loadErrorHelp:
      "If no data is loaded on the server, charts will be unavailable. Contact an administrator to ensure the dataset is configured correctly.",
    participantCountBySchool: "Participant Count by School",
    participantCountBySex: "Participant Count by Sex",
    participantsPerWave: "Participants per Wave (Trend)",
    noDataTitle: "No data available yet.",
    noDataHint:
      "The server may not have a dataset loaded. Check API configuration.",
    gettingStarted: "Getting Started",
    gettingStartedExplore:
      "Use the Explore page to query wellbeing data with privacy-safe blanket suppression.",
    gettingStartedQuery:
      "Use the Query Builder to create custom suppression-query plans.",
    gettingStartedTable:
      "Charts support Show Table for a tabular view and CSV download for raw results.",
    gettingStartedSuppression:
      "Suppressed cells show a hidden value placeholder to protect privacy.",
    gettingStartedAdmin:
      "As an admin, you can manage users and their pre-filters.",
  },
  explore: {
    title: "Explore Data",
    subtitle:
      "Query wellbeing data with blanket suppression for privacy protection",
    queryParameters: "Query Parameters",
    school: "School",
    variable: "Variable",
    variables: "Variables",
    groupBy: "Group By",
    waves: "Waves",
    periods: "Periods",
    periodsObserved: "Periods observed",
    variablesSelected: "Variables selected",
    filters: "Filters",
    neighborType: "Neighbor Type",
    includeNeighborSchools: "Include Neighbor Schools",
    geographical: "Geographical",
    statistical: "Statistical",
    classAggregationNote:
      "Note: Class aggregation is only available for your school. It will be excluded when comparing with neighbors.",
    runQuery: "Run Query",
    querying: "Querying...",
    tryAdjustingFilters:
      "Try adjusting your filters or reducing the number of aggregation dimensions.",
    showingComparison:
      "Showing comparison with {count} neighbor school{plural}.",
    data: "Data",
    mean: "Mean",
    n: "N",
    selectQueryParams:
      'Select your query parameters and click "Run Query" to explore the data.',
    selectAtLeastOneVariable: "Please select at least one variable to query.",
    privacyProtection:
      "Privacy protection is automatically applied through blanket suppression.",
    allDataSuppressed:
      "All data is suppressed due to small group sizes or incompatible versions.",
    multipleCompatibleVersionsNote:
      "Data come from multiple compatible versions of the same questionnaire.",
    rescaledValuesNote:
      "Some values have been rescaled to allow comparison between form versions.",
    lifeSatisfaction: "Life Satisfaction",
    happiness: "Happiness",
    feelingPositive: "Feeling Positive",
    totalWellbeingScore: "Total Wellbeing Score",
    yearGroup: "Year Group",
    sex: "Sex",
    ethnicity: "Ethnicity",
    wave: "Wave",
    class: "Class",
    meanScore: "Mean Score",
  },
  admin: {
    title: "User Management",
    subtitle: "Create, edit and delete dashboard users.",
    newUser: "+ New User",
    loadingUsers: "Loading users…",
    id: "ID",
    username: "Username",
    status: "Status",
    role: "Role",
    scope: "Scope",
    actions: "Actions",
    active: "Active",
    inactive: "Inactive",
    admin: "Admin",
    user: "User",
    students: "{count} student{plural}",
    studentsAll: "{count} student{plural} (all)",
    scopeFiltersFor: "Scope filters for {username}",
    noUsers: "No users found.",
    edit: "Edit",
    delete: "Delete",
    createUser: "Create User",
    editUser: "Edit User",
    scopeJson: "Scope (JSON)",
    scopeHelp: "Pre-filters applied to all queries for this user. Use",
    scopeExample: '{"filters": {}}',
    scopeHelpEnd: "for no restrictions.",
    activeLabel: "Active",
    adminLabel: "Admin",
    cancel: "Cancel",
    saving: "Saving…",
    create: "Create",
    save: "Save",
    deleteUserTitle: "Delete User",
    deleteUserConfirm:
      "Are you sure you want to delete {username}? This cannot be undone.",
    deleting: "Deleting…",
    invalidScopeJson: "Invalid scope JSON.",
  },
  login: {
    title: "GLOW",
    subtitle: "Longitudinal Data Dashboard",
    signIn: "Sign In",
    signingIn: "Signing in…",
    redirecting: "Redirecting to sign in…",
    footer: "GLOW Wellbeing Research · Read-only access",
    loginFailed: "Login failed. Please try again.",
    configError: "Sign-in is not configured. Contact an administrator.",
    demoPickerTitle: "Demo sign-in — choose a role",
    demoAdmin: "Admin",
    devWrc: "WRC",
    devSchool: "School",
    schoolIdLabel: "School ID (optional)",
    schoolIdPlaceholder: "Leave blank for the first school",
    backToLogin: "Back to sign in",
  },
  demo: {
    label: "Demo deployment",
    summary: "Demo — all data is fictional. Click to see login details",
    adminHeading: "Admin",
    adminHint: "On the sign-in page, choose Admin.",
    schoolsHeading: "Schools",
    schoolId: "School ID",
    schoolName: "School name",
    loading: "Loading schools…",
    infoFailed: "Could not load the school list.",
    noSchools: "No schools are loaded.",
    reset: "Reset demo",
    resetTitle: "Reset the demo?",
    resetWarning: "Reset deletes all changes and signs everyone out.",
    cancel: "Cancel",
    confirmReset: "Reset everything",
    resetDone: "Demo reset",
    resetFailed: "Demo reset failed.",
  },
  columns: {
    wave: "Wave",
    uid: "Student ID",
    name: "Name",
    school: "School",
    yearGroup: "Year group",
    schoolYear: "School year",
    class: "Class",
    d_age: "Age",
    d_city: "City",
    d_country: "Country",
    d_sex: "Sex",
    sex: "Sex",
    d_ethnicity: "Ethnicity",
    ethnicity: "Ethnicity",
    d_sexualOrientation: "Sexual orientation",
    d_genderIdentity: "Gender identity",
    school_size_bucket: "School size bucket",
    student_n: "Student count",
    n: "Count",
    bw_wbeing_total: "Psychological wellbeing total (SWEMWBS)",
    ...buildBeeWellColumnLabels(beWellQuestionGroups),
  },
  // API labels for data description endpoint
  // These mirror the columns section but are namespaced under 'api.'
  api: {
    wave: "Wave",
    school: "School",
    yearGroup: "Year group",
    class: "Class",
    d_age: "Age",
    d_city: "City",
    d_country: "Country",
    d_sex: "Sex",
    sex: "Sex",
    d_ethnicity: "Ethnicity",
    ethnicity: "Ethnicity",
    // Add all BeeWell column labels
    bw_wbeing_total: "Psychological wellbeing total (SWEMWBS)",
    bw_migration_total: "Migration background total",
    bw_selfest_total: "Self-esteem total",
    bw_emoreg_total: "Emotion regulation total",
    bw_stress_total: "Stress total",
    bw_coping_total: "Coping total",
    bw_emodies_total: "Emotional difficulties total",
    bw_behav_total: "Behavioural difficulties total",
    bw_unhealthy_total: "Unhealthy food total",
    bw_socmtype_total: "Social media type total",
    bw_activ_total: "Activities total",
    bw_staffrel_total: "Staff relationships total",
    bw_localenv_total: "Local environment total",
    bw_future_total: "Future optimism total",
    bw_plans_total: "Future plans total",
    bw_gmacs_total: "GM active choices total",
    bw_parentsrel_total: "Parent relationships total",
    bw_friends_total: "Friendship quality total",
    bw_discrim_total: "Discrimination total",
    bw_discloc_total: "Discrimination location total",
    bw_bullying_total: "Bullying total",
    bw_mhcontact_total: "Mental health contact total",
    ...buildBeeWellColumnLabels(beWellQuestionGroups),
    phq9_1: "PHQ-9: Little interest or pleasure in doing things",
    phq9_2: "PHQ-9: Feeling down, depressed, or hopeless",
    phq9_3: "PHQ-9: Trouble falling or staying asleep, or sleeping too much",
    phq9_4: "PHQ-9: Feeling tired or having little energy",
    phq9_5: "PHQ-9: Poor appetite or overeating",
    phq9_6: "PHQ-9: Feeling bad about yourself or that you are a failure",
    phq9_7: "PHQ-9: Trouble concentrating on things",
    phq9_8: "PHQ-9: Moving or speaking slowly or being fidgety or restless",
    phq9_9:
      "PHQ-9: Thoughts that you would be better off dead or of hurting yourself",
    phq9_total: "PHQ-9 total",
  },
  forms: {
    bewell_questionnaire: "BeWell questionnaire",
    phq9_questionnaire: "PHQ-9 questionnaire",
    demographics_questionnaire: "Demographics questionnaire",
  },
} as const;

export type Messages = typeof en;
