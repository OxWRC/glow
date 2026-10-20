# ODK Forms Directory

This directory contains ODK form definitions that are automatically uploaded to ODK Central during deployment.

## Supported Formats

Place form files in this directory:

- **XForm XML** (`.xml`) - Standard ODK XForm format
- **XLSForm** (`.xlsx`, `.xls`) - Excel-based form definitions (automatically converted to XML)

## Deployment Behavior

When the deployment tooling runs on a deployed runner:

1. Scans this directory for form files
2. Converts XLSForms to XML using the pyxform service
3. Extracts the `xmlFormId` from each form
4. Checks if the form content has changed (SHA256 hash comparison)
5. Uploads new or changed forms to the default ODK project ("GLOW Data Collection")
6. Tracks form state in `docker-mount-data/.deploy/share/odk-forms-state.json`

## Form Versioning

- ODK Central automatically creates a new version when uploading a form with an existing `xmlFormId`
- Forms are only re-uploaded if their content hash changes
- Unchanged forms are skipped (idempotent deployment)

## Form State Tracking

The deployment tracks forms by `xmlFormId` (not filename) in JSON format:

```json
{
  "my_survey": {
    "hash": "abc123...",
    "filename": "my_survey_v2.xlsx",
    "uploaded": "2026-05-26T14:30:00Z"
  }
}
```

This means you can rename files without triggering re-upload, as long as the `xmlFormId` and content remain the same.

## Example

```bash
./odk-forms/
├── baseline_survey.xlsx     # XLSForm - will be converted to XML
├── followup_survey.xml       # Pre-converted XForm
└── README.md                 # This file
```

## Troubleshooting

**Form upload fails:**
- Check XLSForm syntax with [XLSForm Online](https://getodk.org/xlsform/)
- Ensure `xmlFormId` is unique and valid (alphanumeric + underscore only)
- Check logs: `docker compose logs service`

**Form not appearing in ODK Central:**
- Verify upload succeeded in activation script output
- Check project exists: "GLOW Data Collection"
- Check ODK Central web UI at `https://odk.<your-domain>`

**Form re-uploads on every deployment:**
- Hash is calculated from XML content (after XLSForm conversion)
- Ensure XLSForm settings are deterministic (avoid random values, timestamps)
