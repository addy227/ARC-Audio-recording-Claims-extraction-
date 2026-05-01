# Debugging Guide: AudioFileId Fix

This guide helps you verify that the AudioFileId fixes are working correctly.

## Quick Start

### 1. Test with a Single JSON File (Recommended First Step)

```bash
# List available JSON files
python debug_audiofileid.py --list-files

# Test with a specific file (dry run - no API call)
python debug_audiofileid.py --json-file path/to/your/file.json

# Test with actual API call (use with caution)
python debug_audiofileid.py --json-file path/to/your/file.json --test-insert
```

### 2. Test with app.py (Full Integration Test)

```bash
# Test with a single file
python app.py --test-file path/to/your/file.json

# Process all files in extracted_claims directory
python app.py
```

## What to Look For

### ✅ Success Indicators

1. **AudioFileId Computation**
   - Look for: `[INFO] Computed AudioFileId: <uuid> from storage_id: <storage_id>`
   - This confirms the AudioFileId is being computed correctly

2. **API Request with AudioFileId**
   - Look for: `[INFO] 📤 Sending claim extract record with audio_file_name: <filename>`
   - Check the API payload includes `AudioFileId` field

3. **Successful API Response**
   - Look for: `[INFO] 🟢 API Insert Success → status=success, audio_file_id=<uuid>`
   - No "AudioFileId not found" errors

### ❌ Failure Indicators

1. **Missing AudioFileId**
   - Look for: `[WARNING] Missing audio_file_storage_id in JSON payload. Cannot compute AudioFileId.`
   - **Fix**: Ensure JSON files have `audio_file_storage_id` in `ARRecordingDetails`

2. **Timeout Errors**
   - Look for: `[ERROR] ❌ API request timed out after 60s`
   - **Fix**: Check network connectivity or increase timeout via `API_TIMEOUT_SEC` env var

3. **AudioFileId Not Found (from API)**
   - Look for: `[ERROR] ❌ API Error: AudioFileId not found`
   - **Fix**: Ensure the audio file record exists in `ClaimCallAudioRecordings` table first

## Step-by-Step Debugging Process

### Step 1: Verify JSON Structure

Run the debug script to inspect a JSON file:

```bash
python debug_audiofileid.py --json-file path/to/file.json
```

**Expected Output:**
```
📋 JSON File Structure:
   Audio File Name: <filename>
   Audio File Storage ID: <storage_id>
   Computed AudioFileId: <uuid>
   Claim JSON Blob ID: <blob_id>
```

### Step 2: Check Logs During app.py Execution

When running `app.py`, watch for these log messages:

1. **AudioFileId Computation** (should appear for each file):
   ```
   [DEBUG] [API_xxx] Computed AudioFileId: <uuid> from storage_id: <storage_id>
   ```

2. **API Request** (should include AudioFileId):
   ```
   [INFO] 📤 Sending claim extract record with audio_file_name: <filename>
   ```

3. **API Response** (should show success):
   ```
   [INFO] 🟢 API Insert Success → status=success, audio_file_id=<uuid>
   ```

### Step 3: Monitor for Errors

Check logs for these error patterns:

- `AudioFileId not found` - Should NOT appear anymore
- `Read timed out` - Should be less frequent (timeout increased to 60s)
- `Missing audio_file_storage_id` - Indicates JSON structure issue

## Common Issues and Solutions

### Issue 1: "Missing audio_file_storage_id in JSON payload"

**Cause**: The JSON file doesn't have the `audio_file_storage_id` field.

**Solution**: 
- Check that the claim extraction process is saving `audio_file_storage_id` in the JSON
- Verify the JSON structure matches the expected format

### Issue 2: "API request timed out"

**Cause**: The API is taking longer than 60 seconds to respond.

**Solution**:
- Check network connectivity
- Verify API endpoint is accessible
- Increase timeout: `export API_TIMEOUT_SEC=120`

### Issue 3: "AudioFileId not found" (from API)

**Cause**: The API cannot find the audio file record in the database.

**Solution**:
- Ensure the audio file was inserted into `ClaimCallAudioRecordings` table first
- Verify the `AudioFileId` matches the ID in the database
- Check that the audio file record exists before inserting extract records

## Log File Locations

Logs are typically located in:
- `logs/YYYY-MM-DD/voiclaim.log`

Check the most recent log file for debugging information.

## Testing Checklist

- [ ] Run `debug_audiofileid.py --list-files` to find test files
- [ ] Run `debug_audiofileid.py --json-file <file>` to verify AudioFileId computation
- [ ] Check logs for "Computed AudioFileId" messages
- [ ] Run `app.py --test-file <file>` with a single file
- [ ] Verify no "AudioFileId not found" errors in logs
- [ ] Check API response includes AudioFileId in success message
- [ ] Monitor timeout errors (should be reduced)

## Environment Variables

Make sure these are set correctly:

```bash
# API Configuration
POST_PROCESS_URL_PROD=<your_api_url>
DEPLOYMENT_KEY_PROD=<your_key>
X_VA_SENDERAGENT_ID_PROD=<your_id>

# Database API Configuration
DB_INSERT_API_AUDIO_URL=<audio_recordings_endpoint>
DB_INSERT_API_EXTRACTS_URL=<recording_extracts_endpoint>
DB_INSERT_API_AUTH_TOKEN=<optional_auth_token>
DB_INSERT_API_KEY=<optional_api_key>

# Timeout (optional, default is 60 seconds)
API_TIMEOUT_SEC=60
```

## Need More Help?

1. Check the log files in `logs/` directory
2. Run the debug script with `--test-insert` to see actual API responses
3. Verify JSON file structure matches expected format
4. Check API endpoint accessibility and authentication
