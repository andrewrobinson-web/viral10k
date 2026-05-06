#!/usr/bin/env python3
"""
VIRAL CONTENT DETECTOR v3 - Agorapulse API
============================================
Pulls video view counts from Agorapulse Content Reports,
flags anything over 10K views, exports results to CSV.

Setup:
  1. pip install requests
  2. export AGORAPULSE_API_KEY='your_key'
  3. Fill in ORG_WORKSPACES below
  4. python3 viral_detector.py --discover-orgs   (find IDs)
  5. python3 viral_detector.py --test-one        (verify)
  6. python3 viral_detector.py                   (full scan)
"""

import os
import sys
import time
import csv
from datetime import datetime, timedelta, timezone

# ─── CONFIGURATION ──────────────────────────────────────────────

API_KEY = os.environ.get("AGORAPULSE_API_KEY", "")

# Fill these in after running --discover-orgs
ORG_WORKSPACES = [
    ("290398", "190399"),
    ("217688", "117689"),
    ("510368", "410258"),
    ("377130", "277088"),
    ("352521", "252495"),
]

THRESHOLDS = [
    {"views": 10_000,  "label": "VIRAL",            "slack_channel": "#viral-wins"},
    {"views": 25_000,  "label": "CASE STUDY",       "slack_channel": "#viral-wins"},
    {"views": 50_000,  "label": "LEADERSHIP ALERT",  "slack_channel": "#viral-wins + #leadership"},
    {"views": 100_000, "label": "MEGA VIRAL",        "slack_channel": "#viral-wins + #leadership"},
]

VIRAL_THRESHOLD = 10_000
LOOKBACK_DAYS = 7


# Only scan these platforms (skip Google My Business and other unsupported types)
SCAN_PLATFORMS = {"FACEBOOK_PAGE", "INSTAGRAM", "FACEBOOK_INSTAGRAM", "TIKTOK", "YOUTUBE", "LINKEDIN_COMPANY"}
BASE_URL = "https://api.agorapulse.com"


# ─── API ────────────────────────────────────────────────────────

def api_get(path, params=None, _retries=3):
    import requests
    url = f"{BASE_URL}{path}"
    headers = {"accept": "application/json", "x-api-key": API_KEY}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json()
    elif response.status_code == 429:
        if _retries > 0:
            print("  Rate limited. Waiting 60s...")
            time.sleep(60)
            return api_get(path, params, _retries=_retries - 1)
        else:
            print("  Rate limited. Max retries hit, skipping.")
            return None
    else:
        print(f"  API error {response.status_code}: {response.text[:200]}")
        return None


def get_organizations():
    return api_get("/v1.0/core/organizations")


def get_workspaces(org_id):
    return api_get(f"/v1.0/core/organizations/{org_id}/workspaces")


def get_profiles(org_id, workspace_id):
    data = api_get(f"/v1.0/core/organizations/{org_id}/workspaces/{workspace_id}/profiles")
    if data and isinstance(data, dict) and "profiles" in data:
        return data["profiles"]
    elif isinstance(data, list):
        return data
    return []


def get_content_report(org_id, workspace_id, profile_uid, since_ts, until_ts):
    path = (
        f"/v1.0/report/organizations/{org_id}"
        f"/workspaces/{workspace_id}"
        f"/profiles/{profile_uid}"
        f"/insights/content"
    )
    return api_get(path, {"since": since_ts, "until": until_ts})


# ─── DATA EXTRACTION (matched to real API response) ────────────

def extract_views(content_data, profile_name, profile_type, profile_uid):
    """
    Extract view counts from Agorapulse content report.

    Confirmed field names from actual API response:
    - viewsCount: total views (all content types) — Facebook, YouTube
    - videoViewsCount: video-specific 3s+ views (null for non-video)
    - organicViewsCount / paidViewsCount: breakdown
    - reachCount: unique accounts reached — best proxy for Instagram
    - impressionsCount: total impressions — primary metric for LinkedIn
      (LinkedIn has no viewsCount or videoViewsCount)
    - postUrl: link to post
    - publishingDate: ISO timestamp
    - engagementCount: total engagement
    - text: post caption
    - tags: Agorapulse labels
    """
    results = []
    if not content_data:
        return results

    # Posts are in the "data" array
    posts = []
    if isinstance(content_data, dict) and "data" in content_data:
        posts = content_data["data"]
    elif isinstance(content_data, list):
        posts = content_data

    is_linkedin = profile_type == "LINKEDIN_COMPANY"

    for post in posts:
        views_count   = int(post.get("viewsCount",       0) or 0)
        video_views   = int(post.get("videoViewsCount",  0) or 0)
        organic_views = int(post.get("organicViewsCount",0) or 0)
        paid_views    = int(post.get("paidViewsCount",   0) or 0)
        reach         = int(post.get("reachCount",       0) or 0)
        impressions   = int(post.get("impressionsCount", 0) or 0)

        # LinkedIn has no viewsCount/videoViewsCount — impressionsCount is the
        # correct primary metric. For all other platforms use the highest of
        # viewsCount, videoViewsCount, or reachCount (covers Instagram reels).
        if is_linkedin:
            views_total = impressions
        else:
            views_total = max(views_count, video_views, reach)

        if views_total > 0:
            results.append({
                "client": profile_name,
                "platform": profile_type,
                "profile_uid": profile_uid,
                "post_id": post.get("id", "unknown"),
                "post_url": post.get("postUrl", "N/A"),
                "published_date": post.get("publishingDate", "unknown"),
                "views_total": views_total,
                "views_organic": organic_views,
                "views_paid": paid_views,
                "video_views": video_views,
                "reach": reach,
                "engagement": int(post.get("engagementCount", 0) or 0),
                "likes": int(post.get("likesCount", 0) or 0),
                "comments": int(post.get("commentsCount", 0) or 0),
                "shares": int(post.get("sharesCount", 0) or 0),
                "text_preview": (post.get("text", "") or "")[:80],
                "tags": ",".join(post.get("tags", []) or []),
                "posted_by": post.get("username") or "Client post",
            })

    return results


def classify_viral(video):
    for threshold in reversed(THRESHOLDS):
        if video["views_total"] >= threshold["views"]:
            return threshold
    return None


# ─── DISCOVERY ──────────────────────────────────────────────────

def discover_orgs():
    print("=" * 60)
    print("DISCOVERING ORGANIZATIONS & WORKSPACES")
    print("=" * 60)

    orgs = get_organizations()
    if not orgs:
        print("No organizations found. Check your API key.")
        return

    org_list = orgs if isinstance(orgs, list) else orgs.get("organizations", [orgs])
    print(f"\nFound {len(org_list)} organization(s):\n")

    all_pairs = []
    for org in org_list:
        org_id = org.get("id", org.get("organizationId", "unknown"))
        org_name = org.get("name", org.get("organizationName", "unknown"))
        print(f"  ORG: {org_name} (ID: {org_id})")
        time.sleep(1)
        ws_data = get_workspaces(org_id)
        if ws_data:
            ws_list = ws_data if isinstance(ws_data, list) else ws_data.get("workspaces", [ws_data])
            for ws in ws_list:
                ws_id = ws.get("id", ws.get("workspaceId", "unknown"))
                ws_name = ws.get("name", ws.get("workspaceName", "unknown"))
                print(f"    WORKSPACE: {ws_name} (ID: {ws_id})")
                all_pairs.append((str(org_id), str(ws_id)))
                time.sleep(1)

    print(f"\n{'─' * 60}")
    print("Copy this into ORG_WORKSPACES in the script:\n")
    print("ORG_WORKSPACES = [")
    for org_id, ws_id in all_pairs:
        print(f'    ("{org_id}", "{ws_id}"),')
    print("]")


def test_fb_fields():
    """Dump ALL raw fields from Scott Shannon's Facebook posts to see what Agorapulse returns."""
    print("=" * 60)
    print("TEST: SCOTT SHANNON FACEBOOK RAW FIELDS")
    print("=" * 60)

    target_name = "Scott Shannon"
    target_type = "FACEBOOK_PAGE"

    for org_id, ws_id in ORG_WORKSPACES:
        profiles = get_profiles(org_id, ws_id)
        for p in profiles:
            if p.get("profileName") == target_name and p.get("profileType") == target_type:
                uid = p.get("profileUid")
                print(f"\nFound: {target_name} ({target_type}) - {uid}")
                now = datetime.now(timezone.utc)
                since = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
                until = int(now.timestamp())
                data = get_content_report(org_id, ws_id, uid, since, until)
                posts = data.get("data", []) if isinstance(data, dict) else (data or [])
                print(f"Posts returned: {len(posts)}\n")
                for i, post in enumerate(posts[:10]):
                    print(f"  --- Post {i+1} ---")
                    for k, v in post.items():
                        print(f"    {k}: {v}")
                    print()
                return
    print("Scott Shannon Facebook profile not found in any workspace.")


def discover_profiles():
    print("=" * 60)
    print("DISCOVERING PROFILES")
    print("=" * 60)

    if not ORG_WORKSPACES:
        print("\nORG_WORKSPACES is empty. Run --discover-orgs first.")
        return

    all_profiles = []
    for org_id, ws_id in ORG_WORKSPACES:
        print(f"\nOrg {org_id} / Workspace {ws_id}:")
        profiles = get_profiles(org_id, ws_id)
        print(f"  Found {len(profiles)} profiles")
        for p in profiles:
            uid = p.get("profileUid", "unknown")
            name = p.get("profileName", "unknown")
            ptype = p.get("profileType", "unknown")
            print(f"    {ptype:12s} | {name} ({uid})")
            all_profiles.append({
                "org_id": org_id, "workspace_id": ws_id,
                "profile_uid": uid, "profile_name": name, "profile_type": ptype,
            })
        time.sleep(1)

    with open("all_profiles.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["org_id", "workspace_id",
                                                "profile_uid", "profile_name", "profile_type"])
        writer.writeheader()
        writer.writerows(all_profiles)
    print(f"\nSaved {len(all_profiles)} profiles to: all_profiles.csv")

    platforms = {}
    for p in all_profiles:
        platforms[p["profile_type"]] = platforms.get(p["profile_type"], 0) + 1
    print(f"\nPlatform breakdown:")
    for pt, count in sorted(platforms.items()):
        print(f"  {pt}: {count}")


def test_one_profile():
    print("=" * 60)
    print("TEST: SINGLE PROFILE CONTENT REPORT")
    print("=" * 60)

    if not ORG_WORKSPACES:
        print("\nORG_WORKSPACES is empty.")
        return

    org_id, ws_id = ORG_WORKSPACES[0]
    profiles = get_profiles(org_id, ws_id)
    if not profiles:
        print("No profiles found.")
        return

    profile = profiles[0]
    uid = profile.get("profileUid")
    name = profile.get("profileName")
    ptype = profile.get("profileType")

    print(f"\nTesting: {name} ({ptype}) - {uid}")

    now = datetime.now(timezone.utc)
    since = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    until = int(now.timestamp())

    data = get_content_report(org_id, ws_id, uid, since, until)
    if data:
        posts = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(posts, list):
            print(f"Posts returned: {len(posts)}")
            # Show view counts for each post
            for i, post in enumerate(posts[:5]):
                print(f"\n  Post {i+1}:")
                print(f"    URL: {post.get('postUrl', 'N/A')}")
                print(f"    viewsCount: {post.get('viewsCount')}")
                print(f"    videoViewsCount: {post.get('videoViewsCount')}")
                print(f"    reachCount: {post.get('reachCount')}")
                print(f"    engagementCount: {post.get('engagementCount')}")
            if len(posts) > 5:
                print(f"\n  ... and {len(posts) - 5} more posts")
        print("\nTest complete. Data structure confirmed.")
    else:
        print("No data returned.")


# ─── MAIN SCAN ──────────────────────────────────────────────────

def run_scan():
    print("=" * 60)
    print("VIRAL CONTENT DETECTOR - FULL SCAN")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Lookback: {LOOKBACK_DAYS} days | Threshold: {VIRAL_THRESHOLD:,} views")
    print("=" * 60)

    if not ORG_WORKSPACES:
        print("\nORG_WORKSPACES is empty. Run --help for setup steps.")
        return None

    now = datetime.now(timezone.utc)
    since_ts = int((now - timedelta(days=LOOKBACK_DAYS)).timestamp())
    until_ts = int(now.timestamp())

    all_videos = []
    viral_videos = []
    errors = []
    request_count = 0
    total_profiles = 0

    for org_id, ws_id in ORG_WORKSPACES:
        print(f"\nOrg {org_id} / Workspace {ws_id}")
        profiles = get_profiles(org_id, ws_id)
        request_count += 1

        # Filter to only the platforms we care about
        profiles = [p for p in profiles if p.get("profileType", "") in SCAN_PLATFORMS]

        if not profiles:
            print("  No matching profiles after platform filter, skipping.")
            continue

        print(f"  Scanning {len(profiles)} profiles...")
        total_profiles += len(profiles)

        for i, profile in enumerate(profiles, 1):
            uid = profile.get("profileUid", "unknown")
            name = profile.get("profileName", "unknown")
            ptype = profile.get("profileType", "unknown")

            print(f"  [{i}/{len(profiles)}] {name} ({ptype})...", end=" ", flush=True)

            try:
                data = get_content_report(org_id, ws_id, uid, since_ts, until_ts)
                request_count += 1

                if data:
                    videos = extract_views(data, name, ptype, uid)
                    all_videos.extend(videos)
                    vc = sum(1 for v in videos if v["views_total"] >= VIRAL_THRESHOLD)
                    print(f"{len(videos)} posts, {vc} viral")
                else:
                    print("no data")
            except Exception as e:
                print(f"ERROR: {e}")
                errors.append({"profile_uid": uid, "name": name, "error": str(e)})

            time.sleep(2)
            if request_count % 200 == 0:
                print(f"\n  [Pausing 30s at {request_count} requests]")
                time.sleep(30)

    # Classify
    viral_videos = [v for v in all_videos if v["views_total"] >= VIRAL_THRESHOLD]
    for v in viral_videos:
        tier = classify_viral(v)
        if tier:
            # Cap client self-posts at VIRAL — higher tiers are FMO-only
            if v.get("posted_by") == "Client post" and tier["label"] in ("CASE STUDY", "LEADERSHIP ALERT", "MEGA VIRAL"):
                tier = next(t for t in THRESHOLDS if t["label"] == "VIRAL")
            v["tier_label"] = tier["label"]
            v["tier_views"] = tier["views"]
            v["slack_channel"] = tier["slack_channel"]
    viral_videos.sort(key=lambda v: v["views_total"], reverse=True)

    # Results
    print(f"\n{'=' * 60}")
    print(f"SCAN COMPLETE")
    print(f"Profiles: {total_profiles} | Requests: {request_count}")
    print(f"Posts: {len(all_videos)} | Viral (10K+): {len(viral_videos)}")
    if errors:
        print(f"Errors: {len(errors)}")
    print("=" * 60)

    if viral_videos:
        print(f"\nVIRAL CONTENT:\n")
        for v in viral_videos:
            print(f"  [{v.get('tier_label')}] {v['client']} - {v['platform']}")
            print(f"  Views: {v['views_total']:,} | Reach: {v['reach']:,} | Engagement: {v['engagement']:,}")
            print(f"  URL: {v['post_url']}")
            print(f"  Published: {v['published_date']}")
            print(f"  Preview: {v['text_preview']}")
            print()
    else:
        print("\nNo viral content detected in this scan window.")

    # Export
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    if all_videos:
        fn = f"all_videos_{ts}.csv"
        # Collect ALL possible keys across all rows (some have tier fields, some don't)
        all_keys = []
        seen = set()
        for row in all_videos:
            for k in row.keys():
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)
        with open(fn, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_videos)
        print(f"\nExported: {fn}")

    viral_csv_fn = None
    if viral_videos:
        viral_csv_fn = f"viral_videos_{ts}.csv"
        keys = list(viral_videos[0].keys())
        with open(viral_csv_fn, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(viral_videos)
        print(f"Exported: {viral_csv_fn}")

    if errors:
        fn = f"errors_{ts}.csv"
        with open(fn, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["profile_uid", "name", "error"])
            w.writeheader()
            w.writerows(errors)
        print(f"Exported: {fn}")

    return viral_videos, all_videos, viral_csv_fn


SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0AN0AR9XJ4")
SLACK_BOT_DM_CHANNEL = "D0ALV437AAU"  # Bot's DM channel (fallback upload target)


# ─── SLACK ──────────────────────────────────────────────────────

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

def send_slack_alerts(viral_videos):
    if not SLACK_WEBHOOK_URL:
        print("\nSlack alerts skipped (set SLACK_WEBHOOK_URL to enable)")
        return
    import requests

    tier_emoji = {"VIRAL": "\U0001F525", "CASE STUDY": "\U0001F3AF",
                  "LEADERSHIP ALERT": "\U0001F680", "MEGA VIRAL": "\U0001F4A5"}

    CLIENT_W = 38
    rows = []
    fmo_count = 0
    for v in viral_videos:
        tier = v.get("tier_label", "VIRAL")
        emoji = tier_emoji.get(tier, "\U0001F525")
        client_name = v['client']
        pad = max(0, CLIENT_W - len(client_name)) * ' '
        client_col = f"<{v['post_url']}|{client_name}>{pad}"
        raw_posted_by = str(v.get("posted_by") or "").strip()
        is_fmo = raw_posted_by != "Client post" and bool(raw_posted_by)
        fox = "\U0001F98A" if is_fmo else "  "
        if is_fmo:
            fmo_count += 1
        pub_date = str(v.get("published_date", "") or "").split("T")[0]
        rows.append(f"{emoji} {tier:<16} {client_col} {v['views_total']:>10,}  {pub_date}  {fox}")

    table = "\n".join(rows)
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    fmo_note = f" ({fmo_count} FMO posted \U0001F98A, {len(viral_videos) - fmo_count} client posted)"
    header  = f"   {'TIER':<16} {'CLIENT':<{CLIENT_W}} {'VIEWS':>10}  DATE        POSTED BY"
    divider = "─" * len(header)

    # Webhooks post to a fixed channel baked into the URL — no channel or auth header needed
    payload = {
        "text": f":bar_chart: {len(viral_videos)} viral videos detected",
        "unfurl_links": False,
        "unfurl_media": False,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text",
                "text": f":bar_chart: Viral Scan — {len(viral_videos)} Videos{fmo_note} — {scan_time}"}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"```{header}\n{divider}\n{table}```"}},
        ]
    }
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
    if resp.status_code == 200 and resp.text == "ok":
        print(f"  Slack summary ({len(viral_videos)} videos): sent")
    else:
        print(f"  Slack summary FAILED (HTTP {resp.status_code}: {resp.text})")
        sys.exit(1)  # Fail loudly so GitHub Actions marks the run red


def upload_files_to_slack(csv_fn):
    """Upload the viral CSV to Slack so the team can access it."""
    if not SLACK_BOT_TOKEN:
        print("\nSlack file upload skipped (set SLACK_BOT_TOKEN to enable)")
        return
    channel = SLACK_CHANNEL_ID or SLACK_BOT_DM_CHANNEL

    import requests

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    if not csv_fn:
        return
    filepath, title = csv_fn, "Viral Videos CSV"
    try:
        # Step 1: Get upload URL
        with open(filepath, "rb") as fh:
            file_bytes = fh.read()

        url_resp = requests.get(
            "https://slack.com/api/files.getUploadURLExternal",
            headers=headers,
            params={"filename": filepath, "length": len(file_bytes)},
        )
        url_data = url_resp.json()
        if not url_data.get("ok"):
            print(f"  Upload URL failed for {filepath}: {url_data.get('error')}")
            return

        upload_url = url_data["upload_url"]
        file_id = url_data["file_id"]

        # Step 2: Upload the file
        requests.post(upload_url, data=file_bytes)

        # Step 3: Complete and share to channel
        complete_resp = requests.post(
            "https://slack.com/api/files.completeUploadExternal",
            headers={**headers, "Content-Type": "application/json"},
            json={"files": [{"id": file_id, "title": title}], "channel_id": channel},
        )
        complete_data = complete_resp.json()
        if complete_data.get("ok"):
            print(f"  Uploaded to Slack: {filepath}")
        else:
            print(f"  Complete failed for {filepath}: {complete_data.get('error')}")
    except Exception as e:
        print(f"  Upload error for {filepath}: {e}")


# ─── CLI ────────────────────────────────────────────────────────

def main():
    if not API_KEY:
        print("ERROR: export AGORAPULSE_API_KEY='your_key' first")
        return

    cmd = sys.argv[1] if len(sys.argv) > 1 else "--scan"

    if cmd == "--test-fb":
        test_fb_fields()
    elif cmd == "--discover-orgs":
        discover_orgs()
    elif cmd == "--discover-profiles":
        discover_profiles()
    elif cmd == "--test-one":
        test_one_profile()
    elif cmd == "--scan":
        result = run_scan()
        if result:
            viral, all_vids, viral_csv_fn = result
            if viral:
                send_slack_alerts(viral)
                upload_files_to_slack(viral_csv_fn)
    elif cmd == "--replay-last":
        import glob as _glob
        files = sorted(_glob.glob("viral_videos_*.csv"))
        if not files:
            print("No viral_videos_*.csv found in current directory.")
            return
        fn = files[-1]
        print(f"Replaying: {fn}")
        with open(fn, newline="") as f:
            reader = csv.DictReader(f)
            viral = list(reader)
        for v in viral:
            v["views_total"] = int(v.get("views_total", 0) or 0)
            v["reach"] = int(v.get("reach", 0) or 0)
            v["engagement"] = int(v.get("engagement", 0) or 0)
        send_slack_alerts(viral)
    elif cmd == "--help":
        print("""
VIRAL CONTENT DETECTOR v3
=========================

Run in order:
  1. export AGORAPULSE_API_KEY='your_key'
  2. python3 viral_detector.py --discover-orgs
  3. python3 viral_detector.py --discover-profiles
  4. python3 viral_detector.py --test-one
  5. python3 viral_detector.py

Commands:
  --discover-orgs       List orgs + workspaces
  --discover-profiles   List all profiles (saves CSV)
  --test-one            Test 1 profile content report
  --scan                Full scan (default)
  --replay-last         Re-post last viral CSV to Slack (no scan)
  --help                Show this

Optional:
  export SLACK_WEBHOOK_URL='https://hooks.slack.com/...'
        """)
    else:
        print(f"Unknown: {cmd}. Try --help")

if __name__ == "__main__":
    main()
