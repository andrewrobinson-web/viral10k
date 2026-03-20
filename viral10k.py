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
import json
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
LOOKBACK_DAYS = 90

# Industry groupings for the 3D galaxy showcase.
# Unmapped clients fall into "Other". Update as new clients are onboarded.
CLIENT_INDUSTRIES = {
    # Legal
    "Cellino Law":                             "Legal",
    "D&W Law Group":                           "Legal",
    "Davis & Associates":                      "Legal",
    "Detroit Immigration":                     "Legal",
    "Gitelman Legal Group":                    "Legal",
    "Immigration Law PLLC":                    "Legal",
    "The Leach Firm":                          "Legal",
    "The Perecman Firm *REPORTING ONLY*":      "Legal",
    "Watson Kulman LLC":                       "Legal",
    "harmangreenpc":                           "Legal",
    "Your Divorce Architect":                  "Legal",
    "Preece and Associates":                   "Legal",
    # Mortgage & Finance
    "Cremon Mortgage Experts":                 "Mortgage & Finance",
    "Derek Brickley | Gen-Z Mortgage Advisor": "Mortgage & Finance",
    "Interlinc Mortgage - New Orleans, LA (Poydras)": "Mortgage & Finance",
    "Jet Direct Mortgage":                     "Mortgage & Finance",
    "Major Money Matters":                     "Mortgage & Finance",
    "The Mortgage Hunter":                     "Mortgage & Finance",
    "Stephen C. Gaubert":                      "Mortgage & Finance",
    # Real Estate
    "Beaudoin Realty Group":                   "Real Estate",
    "Elizabeth Fry Team - Intero Showcase":    "Real Estate",
    "Ford Brothers Inc. Auctioneers":          "Real Estate",
    "Greg Gale":                               "Real Estate",
    "The Nick Barta Team":                     "Real Estate",
    "Weichert Realtors Ford Brothers":         "Real Estate",
    "Richard Quintana":                        "Real Estate",
    "Charly Bates":                            "Real Estate",
    # Food & Hospitality
    "Buttermilks Farmhouse":                   "Food & Hospitality",
    "Capo Ristorante":                         "Food & Hospitality",
    # Creators & Media
    "Brad Thomson | Spanish":                  "Creators & Media",
    "FMO Media":                               "Creators & Media",
}

# Only scan these platforms (skip LinkedIn, Google, etc.)
SCAN_PLATFORMS = {"FACEBOOK_PAGE", "INSTAGRAM", "TIKTOK", "YOUTUBE"}
BASE_URL = "https://api.agorapulse.com"


# ─── API ────────────────────────────────────────────────────────

def api_get(path, params=None):
    import requests
    url = f"{BASE_URL}{path}"
    headers = {"accept": "application/json", "x-api-key": API_KEY}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        return response.json()
    elif response.status_code == 429:
        print("  Rate limited. Waiting 60s...")
        time.sleep(60)
        return api_get(path, params)
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
    - viewsCount: total views (all content types)
    - videoViewsCount: video-specific 3s+ views (null for non-video)
    - organicViewsCount / paidViewsCount: breakdown
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

    for post in posts:
        # Use viewsCount as the primary metric (covers all content)
        # For video-specific: videoViewsCount (but it's null for non-video posts)
        views_count = int(post.get("viewsCount", 0) or 0)
        video_views = int(post.get("videoViewsCount", 0) or 0)
        organic_views = int(post.get("organicViewsCount", 0) or 0)
        paid_views = int(post.get("paidViewsCount", 0) or 0)

        # Use the higher of viewsCount or videoViewsCount
        # viewsCount = all impressions; videoViewsCount = 3s+ video views
        # For viral detection, viewsCount is the right metric
        views = views_count

        if views > 0:
            results.append({
                "client": profile_name,
                "platform": profile_type,
                "profile_uid": profile_uid,
                "post_id": post.get("id", "unknown"),
                "post_url": post.get("postUrl", "N/A"),
                "published_date": post.get("publishingDate", "unknown"),
                "views_total": views_count,
                "views_organic": organic_views,
                "views_paid": paid_views,
                "video_views": video_views,
                "reach": int(post.get("reachCount", 0) or 0),
                "engagement": int(post.get("engagementCount", 0) or 0),
                "likes": int(post.get("likeCount", 0) or 0),
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
            if v.get("posted_by") == "Client post" and tier["label"] in ("CASE STUDY", "LEADERSHIP ALERT"):
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


# ─── INTERACTIVE HTML REPORT ────────────────────────────────────

def generate_report(viral_videos, all_videos, scan_time=None):
    """Generate an interactive HTML dashboard of viral content."""
    if not viral_videos:
        print("\nNo viral videos to report on.")
        return

    ts = scan_time or datetime.now().strftime("%Y%m%d_%H%M")
    total_posts = len(all_videos)
    total_viral = len(viral_videos)
    total_views = sum(v["views_total"] for v in viral_videos)

    # Platform breakdown
    platform_counts = {}
    platform_views = {}
    for v in viral_videos:
        p = v["platform"]
        platform_counts[p] = platform_counts.get(p, 0) + 1
        platform_views[p] = platform_views.get(p, 0) + v["views_total"]

    # Leaderboard data (sorted by views)
    leaderboard = sorted(viral_videos, key=lambda v: v["views_total"], reverse=True)

    # Timeline data
    timeline_data = []
    for v in viral_videos:
        date_str = v.get("published_date", "")
        if "T" in str(date_str):
            date_str = str(date_str).split("T")[0]
        timeline_data.append({
            "date": date_str,
            "views": v["views_total"],
            "client": v["client"],
            "platform": v["platform"],
            "url": v["post_url"],
        })
    timeline_data.sort(key=lambda x: x["date"])

    # Build leaderboard rows
    leaderboard_rows = ""
    for i, v in enumerate(leaderboard, 1):
        tier = v.get("tier_label", "VIRAL")
        tier_class = tier.lower().replace(" ", "-")
        bar_width = (v["views_total"] / leaderboard[0]["views_total"]) * 100
        leaderboard_rows += f"""
        <tr class="leaderboard-row" onclick="window.open('{v['post_url']}', '_blank')">
            <td class="rank">#{i}</td>
            <td class="client-cell">
                <div class="client-name">{v['client']}</div>
                <div class="platform-tag {v['platform'].lower()}">{v['platform'].replace('_PAGE','').replace('_',' ')}</div>
            </td>
            <td class="views-cell">
                <div class="views-bar-container">
                    <div class="views-bar {tier_class}" style="width: {bar_width}%"></div>
                </div>
                <div class="views-number">{v['views_total']:,}</div>
            </td>
            <td class="tier-cell"><span class="tier-badge {tier_class}">{tier}</span></td>
            <td class="engagement-cell">{v['engagement']:,}</td>
            <td class="date-cell">{str(v.get('published_date','')).split('T')[0]}</td>
        </tr>"""

    # Platform chart data
    platform_labels = json.dumps(list(platform_counts.keys()))
    platform_count_data = json.dumps(list(platform_counts.values()))
    platform_view_data = json.dumps(list(platform_views.values()))
    platform_colors = {
        "TIKTOK": "#00f2ea",
        "FACEBOOK_PAGE": "#1877f2",
        "INSTAGRAM": "#e4405f",
        "YOUTUBE": "#ff0000",
    }
    colors_list = json.dumps([platform_colors.get(p, "#888888") for p in platform_counts.keys()])

    # Timeline chart data
    timeline_labels = json.dumps([t["date"] for t in timeline_data])
    timeline_views = json.dumps([t["views"] for t in timeline_data])
    timeline_clients = json.dumps([f"{t['client']} ({t['platform'].replace('_PAGE','').replace('_',' ')})" for t in timeline_data])
    timeline_colors = json.dumps([platform_colors.get(t["platform"], "#888888") for t in timeline_data])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Viral Content Report - {ts}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f1117;
        color: #e1e1e6;
        padding: 24px;
        min-height: 100vh;
    }}
    .header {{
        text-align: center;
        padding: 40px 0 32px;
        border-bottom: 1px solid #2a2a3a;
        margin-bottom: 32px;
    }}
    .header h1 {{
        font-size: 2.2em;
        font-weight: 700;
        background: linear-gradient(135deg, #ff6b35, #ff2e63);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 8px;
    }}
    .header .subtitle {{
        color: #888;
        font-size: 0.95em;
    }}
    .stats-row {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-bottom: 32px;
    }}
    .stat-card {{
        background: #1a1b26;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
        border: 1px solid #2a2a3a;
    }}
    .stat-number {{
        font-size: 2em;
        font-weight: 700;
        color: #ff6b35;
    }}
    .stat-label {{
        color: #888;
        font-size: 0.85em;
        margin-top: 4px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .section {{
        background: #1a1b26;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 24px;
        border: 1px solid #2a2a3a;
    }}
    .section h2 {{
        font-size: 1.3em;
        margin-bottom: 20px;
        color: #fff;
    }}
    .charts-row {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 24px;
        margin-bottom: 24px;
    }}
    @media (max-width: 768px) {{
        .charts-row {{ grid-template-columns: 1fr; }}
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
    }}
    th {{
        text-align: left;
        padding: 12px 8px;
        color: #888;
        font-size: 0.8em;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        border-bottom: 1px solid #2a2a3a;
    }}
    .leaderboard-row {{
        cursor: pointer;
        transition: background 0.15s;
    }}
    .leaderboard-row:hover {{
        background: #252636;
    }}
    .leaderboard-row td {{
        padding: 14px 8px;
        border-bottom: 1px solid #1f1f2e;
        vertical-align: middle;
    }}
    .rank {{
        font-weight: 700;
        color: #666;
        width: 50px;
    }}
    .client-cell {{
        min-width: 180px;
    }}
    .client-name {{
        font-weight: 600;
        color: #fff;
        margin-bottom: 4px;
    }}
    .platform-tag {{
        display: inline-block;
        font-size: 0.7em;
        padding: 2px 8px;
        border-radius: 4px;
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.5px;
    }}
    .platform-tag.tiktok {{ background: rgba(0,242,234,0.15); color: #00f2ea; }}
    .platform-tag.facebook_page {{ background: rgba(24,119,242,0.15); color: #1877f2; }}
    .platform-tag.instagram {{ background: rgba(228,64,95,0.15); color: #e4405f; }}
    .platform-tag.youtube {{ background: rgba(255,0,0,0.15); color: #ff4444; }}
    .views-cell {{
        min-width: 200px;
    }}
    .views-bar-container {{
        background: #252636;
        border-radius: 4px;
        height: 8px;
        margin-bottom: 4px;
        overflow: hidden;
    }}
    .views-bar {{
        height: 100%;
        border-radius: 4px;
        transition: width 0.6s ease;
    }}
    .views-bar.viral {{ background: #ff6b35; }}
    .views-bar.case-study {{ background: #ff2e63; }}
    .views-bar.leadership-alert {{ background: #a855f7; }}
    .views-bar.mega-viral {{ background: #ef4444; }}
    .views-number {{
        font-weight: 600;
        font-size: 0.9em;
    }}
    .tier-badge {{
        display: inline-block;
        font-size: 0.7em;
        padding: 3px 10px;
        border-radius: 6px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .tier-badge.viral {{ background: rgba(255,107,53,0.15); color: #ff6b35; }}
    .tier-badge.case-study {{ background: rgba(255,46,99,0.15); color: #ff2e63; }}
    .tier-badge.leadership-alert {{ background: rgba(168,85,247,0.15); color: #a855f7; }}
    .tier-badge.mega-viral {{ background: rgba(239,68,68,0.15); color: #ef4444; }}
    .engagement-cell {{ color: #888; }}
    .date-cell {{ color: #888; font-size: 0.9em; }}
    .footer {{
        text-align: center;
        padding: 24px;
        color: #444;
        font-size: 0.8em;
    }}
    canvas {{ max-height: 300px; }}
</style>
</head>
<body>

<div class="header">
    <h1>Viral Content Report</h1>
    <div class="subtitle">Scan: {ts} | Lookback: {LOOKBACK_DAYS} days | Threshold: {VIRAL_THRESHOLD:,} views</div>
</div>

<div class="stats-row">
    <div class="stat-card">
        <div class="stat-number">{total_viral}</div>
        <div class="stat-label">Viral Videos</div>
    </div>
    <div class="stat-card">
        <div class="stat-number">{total_views:,}</div>
        <div class="stat-label">Total Viral Views</div>
    </div>
    <div class="stat-card">
        <div class="stat-number">{total_posts:,}</div>
        <div class="stat-label">Posts Scanned</div>
    </div>
    <div class="stat-card">
        <div class="stat-number">{leaderboard[0]['views_total']:,}</div>
        <div class="stat-label">Top Video Views</div>
    </div>
</div>

<div class="charts-row">
    <div class="section">
        <h2>Platform Breakdown</h2>
        <canvas id="platformChart"></canvas>
    </div>
    <div class="section">
        <h2>Viral Timeline</h2>
        <canvas id="timelineChart"></canvas>
    </div>
</div>

<div class="section">
    <h2>Viral Leaderboard</h2>
    <table>
        <thead>
            <tr>
                <th>Rank</th>
                <th>Client</th>
                <th>Views</th>
                <th>Tier</th>
                <th>Engagement</th>
                <th>Published</th>
            </tr>
        </thead>
        <tbody>
            {leaderboard_rows}
        </tbody>
    </table>
</div>

<div class="footer">
    Generated by Viral10K Tracker | Click any row to view the post
</div>

<script>
// Platform donut chart
new Chart(document.getElementById('platformChart'), {{
    type: 'doughnut',
    data: {{
        labels: {platform_labels},
        datasets: [{{
            data: {platform_view_data},
            backgroundColor: {colors_list},
            borderWidth: 0,
            hoverOffset: 8,
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: true,
        plugins: {{
            legend: {{
                position: 'bottom',
                labels: {{ color: '#888', padding: 16, font: {{ size: 12 }} }}
            }},
            tooltip: {{
                callbacks: {{
                    label: function(ctx) {{
                        return ctx.label.replace('_PAGE','') + ': ' + ctx.parsed.toLocaleString() + ' views';
                    }}
                }}
            }}
        }}
    }}
}});

// Timeline scatter chart
new Chart(document.getElementById('timelineChart'), {{
    type: 'bar',
    data: {{
        labels: {timeline_labels},
        datasets: [{{
            data: {timeline_views},
            backgroundColor: {timeline_colors},
            borderRadius: 6,
            borderSkipped: false,
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: true,
        plugins: {{
            legend: {{ display: false }},
            tooltip: {{
                callbacks: {{
                    title: function(ctx) {{
                        var clients = {timeline_clients};
                        return clients[ctx[0].dataIndex];
                    }},
                    label: function(ctx) {{
                        return ctx.parsed.y.toLocaleString() + ' views';
                    }}
                }}
            }}
        }},
        scales: {{
            x: {{
                ticks: {{ color: '#666', font: {{ size: 10 }} }},
                grid: {{ display: false }}
            }},
            y: {{
                ticks: {{
                    color: '#666',
                    callback: function(v) {{ return (v/1000) + 'K'; }}
                }},
                grid: {{ color: '#1f1f2e' }}
            }}
        }}
    }}
}});
</script>

</body>
</html>"""

    fn = f"viral_report_{ts}.html"
    with open(fn, "w") as f:
        f.write(html)
    print(f"Report generated: {fn}")

    # Auto-open in browser on Mac
    import platform as plat
    if plat.system() == "Darwin":
        os.system(f"open {fn}")
    elif plat.system() == "Windows":
        os.system(f"start {fn}")
    else:
        print(f"Open {fn} in your browser to view.")


SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
SLACK_BOT_DM_CHANNEL = "D0ALV437AAU"  # Bot's DM used for thumbnail fetching


def fetch_thumbnails_from_slack(viral_videos):
    """
    Post links via bot token to a DM the bot owns,
    wait for Slack to unfurl them, read back the thumbnail URLs,
    download and base64 encode them.
    """
    if not SLACK_BOT_TOKEN:
        print("\nSlack thumbnail fetch skipped (set SLACK_BOT_TOKEN to enable)")
        return

    import requests
    import base64

    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    # Step 1: Use the bot's working DM channel
    channel_id = SLACK_BOT_DM_CHANNEL
    print(f"\nUsing bot DM channel: {channel_id}")

    # Step 2: Post links in batches of 5 (Slack only unfurls 5 links per message)
    urls = [v.get("post_url", "") for v in viral_videos if v.get("post_url")]
    batch_size = 5
    batches = [urls[i:i + batch_size] for i in range(0, len(urls), batch_size)]
    print(f"\nPosting {len(urls)} links in {len(batches)} batches of {batch_size} for unfurling...")

    url_to_thumb = {}
    message_timestamps = []

    for batch_num, batch in enumerate(batches, 1):
        links_text = "\n".join(batch)
        post_resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=headers,
            json={
                "channel": channel_id,
                "text": links_text,
                "unfurl_links": True,
                "unfurl_media": True,
            },
        )
        post_data = post_resp.json()
        if not post_data.get("ok"):
            print(f"  Batch {batch_num} post failed: {post_data.get('error')}")
            continue

        message_ts = post_data.get("ts")
        message_timestamps.append(message_ts)
        print(f"  Batch {batch_num}/{len(batches)} posted (ts: {message_ts}), waiting 15s...")
        time.sleep(15)

        hist_resp = requests.get(
            "https://slack.com/api/conversations.history",
            headers=headers,
            params={
                "channel": channel_id,
                "oldest": message_ts,
                "latest": message_ts,
                "inclusive": True,
                "limit": 1,
            },
        )
        hist_data = hist_resp.json()
        if not hist_data.get("ok"):
            print(f"  Batch {batch_num} read failed: {hist_data.get('error')}")
            continue

        messages = hist_data.get("messages", [])
        if not messages:
            continue

        attachments = messages[0].get("attachments", [])
        for att in attachments:
            original_url = att.get("original_url", att.get("from_url", att.get("url", "")))
            thumb = att.get("image_url") or att.get("thumb_url") or ""
            if original_url and thumb:
                url_to_thumb[original_url] = thumb

        print(f"    Got {len(attachments)} unfurls (total so far: {len(url_to_thumb)})")

    # Step 5: Download thumbnails and base64 encode
    success = 0
    for v in viral_videos:
        post_url = v.get("post_url", "")
        thumb_url = None

        if post_url in url_to_thumb:
            thumb_url = url_to_thumb[post_url]
        else:
            for slack_url, thumb in url_to_thumb.items():
                if post_url in slack_url or slack_url in post_url:
                    thumb_url = thumb
                    break

        if thumb_url:
            print(f"  {v['client']}...", end=" ", flush=True)
            try:
                img_resp = requests.get(thumb_url, timeout=10, allow_redirects=True)
                if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                    content_type = img_resp.headers.get("Content-Type", "image/jpeg")
                    if ";" in content_type:
                        content_type = content_type.split(";")[0].strip()
                    b64 = base64.b64encode(img_resp.content).decode("utf-8")
                    v["thumbnail"] = f"data:{content_type};base64,{b64}"
                    success += 1
                    print("got it")
                else:
                    v["thumbnail"] = ""
                    print(f"failed (status {img_resp.status_code})")
            except Exception as e:
                v["thumbnail"] = ""
                print(f"failed ({e})")
        else:
            v["thumbnail"] = ""

    print(f"  Thumbnails captured: {success}/{len(viral_videos)}")

    # Step 6: Clean up all batch messages
    for ts in message_timestamps:
        requests.post(
            "https://slack.com/api/chat.delete",
            headers=headers,
            json={"channel": channel_id, "ts": ts},
        )
    print(f"  Cleaned up {len(message_timestamps)} temp Slack messages")


def generate_3d_showcase(viral_videos, scan_time=None):
    """3D floating-sphere showcase with industry colour-coding and filter dropdowns."""
    if not viral_videos:
        print("\nNo viral videos for 3D showcase.")
        return

    if not any(v.get("thumbnail") for v in viral_videos):
        fetch_thumbnails_from_slack(viral_videos)

    ts = scan_time or datetime.now().strftime("%Y%m%d_%H%M")

    max_views = max(v["views_total"] for v in viral_videos)

    industry_colors = {
        "Legal":              "#f5c842",
        "Mortgage & Finance": "#00c896",
        "Real Estate":        "#b06cff",
        "Food & Hospitality": "#ff6b35",
        "Creators & Media":   "#00f2ff",
        "Other":              "#888888",
    }
    tier_emojis = {
        "MEGA VIRAL":       "\U0001F4A5",
        "LEADERSHIP ALERT": "\U0001F680",
        "CASE STUDY":       "\U0001F3AF",
        "VIRAL":            "\U0001F525",
    }
    platform_display = {
        "TIKTOK": "TikTok", "FACEBOOK_PAGE": "Facebook",
        "INSTAGRAM": "Instagram", "YOUTUBE": "YouTube",
    }

    spheres_data = []
    industries_seen = set()
    for v in viral_videos:
        industry = CLIENT_INDUSTRIES.get(v["client"], "Other")
        industries_seen.add(industry)
        ratio = v["views_total"] / max_views
        radius = 0.4 + (ratio * 2.0)
        ic = industry_colors.get(industry, "#888888")
        spheres_data.append({
            "client":     v["client"],
            "platform":   platform_display.get(v["platform"], v["platform"]),
            "views":      v["views_total"],
            "engagement": int(v.get("engagement") or 0),
            "url":        v.get("post_url", "#"),
            "date":       str(v.get("published_date", "")).split("T")[0],
            "tier":       v.get("tier_label", "VIRAL"),
            "emoji":      tier_emojis.get(v.get("tier_label", "VIRAL"), "\U0001F525"),
            "thumbnail":  v.get("thumbnail", ""),
            "radius":     round(radius, 2),
            "color":      ic,
            "industry":   industry,
            "fmo":        v.get("posted_by") != "Client post",
        })

    spheres_json = json.dumps(spheres_data)
    industries_list = json.dumps(sorted(industries_seen))
    total_views = sum(v["views_total"] for v in viral_videos)
    n_videos = len(viral_videos)
    scan_date = datetime.now().strftime("%B %d, %Y")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Viral10K - 3D Showcase</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #000; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #fff; }}
    canvas {{ display: block; }}
    #title {{ position: fixed; top: 24px; left: 50%; transform: translateX(-50%); text-align: center; z-index: 10; pointer-events: none; }}
    #title h1 {{ font-size: 1.6em; font-weight: 700; background: linear-gradient(135deg, #ff6b35, #ff2e63); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    #title .sub {{ color: #555; font-size: 0.8em; margin-top: 4px; }}
    #filters {{
        position: fixed; top: 20px; right: 24px; z-index: 20;
        display: flex; flex-direction: column; gap: 8px; align-items: flex-end;
    }}
    #filters select {{
        background: rgba(12,14,22,0.92); color: #ccc;
        border: 1px solid #2a2a3a; border-radius: 8px;
        padding: 7px 12px; font-size: 0.78em; cursor: pointer;
        outline: none; min-width: 170px;
        appearance: none; -webkit-appearance: none;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23666'/%3E%3C/svg%3E");
        background-repeat: no-repeat; background-position: right 10px center;
        padding-right: 28px;
    }}
    #filters select:hover {{ border-color: #ff6b35; color: #fff; }}
    #count {{ font-size: 0.7em; color: #444; text-align: right; }}
    #tooltip {{ position: fixed; display: none; background: rgba(15,17,23,0.95); border: 1px solid #2a2a3a; border-radius: 12px; padding: 16px 20px; pointer-events: none; z-index: 100; min-width: 220px; backdrop-filter: blur(10px); }}
    #tooltip .tt-client {{ font-size: 1.1em; font-weight: 700; margin-bottom: 2px; }}
    #tooltip .tt-industry {{ font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
    #tooltip .tt-platform {{ display: inline-block; font-size: 0.7em; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 10px; }}
    #tooltip .tt-stat {{ color: #888; font-size: 0.85em; margin-bottom: 3px; }}
    #tooltip .tt-stat span {{ color: #fff; font-weight: 600; }}
    #tooltip .tt-tier {{ margin-top: 8px; font-size: 0.9em; font-weight: 700; }}
    #tooltip .tt-hint {{ color: #444; font-size: 0.75em; margin-top: 8px; }}
    #legend {{ position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); display: flex; flex-wrap: wrap; justify-content: center; gap: 16px; z-index: 10; background: rgba(15,17,23,0.85); padding: 10px 20px; border-radius: 8px; border: 1px solid #1a1a2a; max-width: 90vw; }}
    .li {{ display: flex; align-items: center; gap: 6px; font-size: 0.78em; color: #888; white-space: nowrap; }}
    .ld {{ width: 10px; height: 10px; border-radius: 50%; }}
    #instructions {{ position: fixed; top: 80px; left: 50%; transform: translateX(-50%); color: #333; font-size: 0.75em; z-index: 10; pointer-events: none; transition: opacity 2s; }}
</style>
</head>
<body>

<div id="title">
    <h1>Viral10K Showcase</h1>
    <div class="sub">{n_videos} viral posts &bull; {total_views:,} views &bull; {scan_date}</div>
</div>

<div id="filters">
    <select id="fmo-filter">
        <option value="all">All Posts</option>
        <option value="fmo">FMO Produced</option>
        <option value="client">Client Posts</option>
    </select>
    <select id="industry-filter">
        <option value="all">All Industries</option>
    </select>
    <div id="count"></div>
</div>

<div id="instructions">Drag to orbit &bull; Scroll to zoom &bull; Hover for details &bull; Click to open post</div>

<div id="tooltip">
    <div class="tt-client" id="tt-client"></div>
    <div class="tt-industry" id="tt-industry"></div>
    <div class="tt-platform" id="tt-platform"></div>
    <div class="tt-stat">Views: <span id="tt-views"></span></div>
    <div class="tt-stat">Engagement: <span id="tt-engagement"></span></div>
    <div class="tt-stat">Published: <span id="tt-date"></span></div>
    <div class="tt-tier" id="tt-tier"></div>
    <div class="tt-hint">Click to open post</div>
</div>

<div id="legend">
    <div class="li"><div class="ld" style="background:#f5c842"></div>Legal</div>
    <div class="li"><div class="ld" style="background:#00c896"></div>Mortgage &amp; Finance</div>
    <div class="li"><div class="ld" style="background:#b06cff"></div>Real Estate</div>
    <div class="li"><div class="ld" style="background:#ff6b35"></div>Food &amp; Hospitality</div>
    <div class="li"><div class="ld" style="background:#00f2ff"></div>Creators &amp; Media</div>
    <div class="li"><div class="ld" style="background:#888"></div>Other</div>
    <div class="li" style="color:#444">|&nbsp;&#x1F98A;&nbsp;<span style="color:#FF6B35">FMO</span></div>
    <div class="li" style="color:#444">| Size = Views</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const SPHERES_DATA = {spheres_json};
const INDUSTRIES = {industries_list};

// Populate industry dropdown
const indSel = document.getElementById('industry-filter');
INDUSTRIES.forEach(function(ind) {{
    var opt = document.createElement('option');
    opt.value = ind; opt.textContent = ind;
    indSel.appendChild(opt);
}});

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
camera.position.set(0, 2, 12);

const renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true }});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setClearColor(0x000000);
document.body.appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0x222233, 0.5));
const pl1 = new THREE.PointLight(0xffffff, 1.2, 100);
pl1.position.set(5, 8, 10); scene.add(pl1);
const pl2 = new THREE.PointLight(0x4444ff, 0.6, 100);
pl2.position.set(-8, -4, -6); scene.add(pl2);

// Stars
const starsPos = [];
for (let i = 0; i < 2000; i++) {{
    starsPos.push((Math.random()-.5)*100, (Math.random()-.5)*100, (Math.random()-.5)*100);
}}
const starsGeo = new THREE.BufferGeometry();
starsGeo.setAttribute('position', new THREE.Float32BufferAttribute(starsPos, 3));
scene.add(new THREE.Points(starsGeo, new THREE.PointsMaterial({{ color: 0x222244, size: 0.1 }})));

const textureLoader = new THREE.TextureLoader();
textureLoader.crossOrigin = 'anonymous';

const allSpheres = [];
const foxEntries = [];
const sphereGroup = new THREE.Group();
const tmpVec = new THREE.Vector3();

function createSphere(data, i) {{
    const geo = new THREE.SphereGeometry(data.radius, 64, 64);
    let mat;
    if (data.thumbnail) {{
        mat = new THREE.MeshPhongMaterial({{
            color: 0xffffff, emissive: new THREE.Color(data.color),
            emissiveIntensity: 0.08, shininess: 40, specular: 0x222222,
            transparent: true, opacity: 0.95,
        }});
        textureLoader.load(data.thumbnail,
            function(tex) {{ tex.wrapS = THREE.RepeatWrapping; mat.map = tex; mat.needsUpdate = true; }},
            undefined, function() {{}}
        );
    }} else {{
        mat = new THREE.MeshPhongMaterial({{
            color: new THREE.Color(data.color), emissive: new THREE.Color(data.color),
            emissiveIntensity: 0.15, shininess: 80, specular: 0x444444,
            transparent: true, opacity: 0.9,
        }});
    }}

    const mesh = new THREE.Mesh(geo, mat);
    const phi = Math.acos(2 * Math.random() - 1);
    const theta = Math.random() * Math.PI * 2;
    const spread = 3 + Math.random() * 4;
    mesh.position.set(
        spread * Math.sin(phi) * Math.cos(theta),
        spread * Math.sin(phi) * Math.sin(theta) * 0.6,
        spread * Math.cos(phi)
    );

    mesh.userData = Object.assign({{}}, data);
    mesh.userData.index = i;
    mesh.userData.baseEmissive = data.thumbnail ? 0.08 : 0.15;
    mesh.userData.bobSpeed  = 0.3 + Math.random() * 0.5;
    mesh.userData.bobOffset = Math.random() * Math.PI * 2;
    mesh.userData.bobAmount = 0.1 + Math.random() * 0.3;
    mesh.userData.baseY = mesh.position.y;

    // Client label
    const lc = document.createElement('canvas'); lc.width = 512; lc.height = 96;
    const lctx = lc.getContext('2d');
    lctx.shadowColor = '#000'; lctx.shadowBlur = 8;
    lctx.fillStyle = '#fff'; lctx.font = 'bold 28px sans-serif';
    lctx.textAlign = 'center';
    lctx.fillText(data.client, 256, 32);
    lctx.fillStyle = data.color; lctx.font = '22px sans-serif';
    lctx.fillText(data.views.toLocaleString() + ' views', 256, 64);
    const label = new THREE.Sprite(new THREE.SpriteMaterial({{ map: new THREE.CanvasTexture(lc), transparent: true, depthWrite: false }}));
    label.scale.set(data.radius * 3, data.radius * 0.75, 1);
    label.position.y = data.radius + 0.4;
    mesh.add(label);

    // Glow ring
    const ring = new THREE.Mesh(
        new THREE.RingGeometry(data.radius * 1.05, data.radius * 1.15, 64),
        new THREE.MeshBasicMaterial({{ color: new THREE.Color(data.color), transparent: true, opacity: 0.2, side: THREE.DoubleSide }})
    );
    mesh.userData.glowRing = ring;
    mesh.add(ring);

    // FMO fox
    if (data.fmo) {{
        const fc = document.createElement('canvas'); fc.width = 128; fc.height = 128;
        const fctx = fc.getContext('2d');
        fctx.beginPath(); fctx.arc(64,64,56,0,Math.PI*2);
        fctx.fillStyle = '#FF6B35'; fctx.fill();
        fctx.strokeStyle = 'rgba(255,255,255,0.8)'; fctx.lineWidth = 5; fctx.stroke();
        fctx.font = '72px serif'; fctx.textAlign = 'center'; fctx.textBaseline = 'middle';
        fctx.fillText('\U0001F98A', 64, 68);
        const fox = new THREE.Sprite(new THREE.SpriteMaterial({{ map: new THREE.CanvasTexture(fc), transparent: true, depthWrite: false }}));
        fox.scale.set(data.radius * 0.42, data.radius * 0.42, 1);
        fox.userData.orbitAngle = Math.random() * Math.PI * 2;
        fox.userData.orbitSpeed = 1.0 + Math.random() * 0.6;
        scene.add(fox);
        foxEntries.push({{ sprite: fox, mesh: mesh }});
    }}

    sphereGroup.add(mesh);
    allSpheres.push(mesh);
}}

SPHERES_DATA.forEach(function(data, i) {{ createSphere(data, i); }});
scene.add(sphereGroup);

// ── Filtering ──────────────────────────────────────────────────
function applyFilters() {{
    const fmoVal = document.getElementById('fmo-filter').value;
    const indVal = document.getElementById('industry-filter').value;
    let visible = 0;
    allSpheres.forEach(function(s) {{
        const d = s.userData;
        const fmoOk = fmoVal === 'all' || (fmoVal === 'fmo' && d.fmo) || (fmoVal === 'client' && !d.fmo);
        const indOk = indVal === 'all' || d.industry === indVal;
        const show = fmoOk && indOk;
        s.visible = show;
        // keep fox in sync
        foxEntries.forEach(function(fe) {{
            if (fe.mesh === s) fe.sprite.visible = show;
        }});
        if (show) visible++;
    }});
    document.getElementById('count').textContent = visible + ' posts shown';
}}

document.getElementById('fmo-filter').addEventListener('change', applyFilters);
document.getElementById('industry-filter').addEventListener('change', applyFilters);
applyFilters();

// ── Camera orbit ───────────────────────────────────────────────
let isDragging = false, prevMouse = {{ x:0, y:0 }};
let orbitAngle = {{ x:0, y:0.3 }}, targetOrbit = {{ x:0, y:0.3 }};
let cameraDistance = 12, targetDistance = 12;

renderer.domElement.addEventListener('mousedown', function(e) {{ isDragging = true; prevMouse = {{ x:e.clientX, y:e.clientY }}; }});
renderer.domElement.addEventListener('mousemove', function(e) {{
    if (isDragging) {{
        targetOrbit.x += (e.clientX - prevMouse.x) * 0.005;
        targetOrbit.y = Math.max(-1, Math.min(1, targetOrbit.y + (e.clientY - prevMouse.y) * 0.005));
        prevMouse = {{ x:e.clientX, y:e.clientY }};
    }}
}});
renderer.domElement.addEventListener('mouseup', function() {{ isDragging = false; }});
renderer.domElement.addEventListener('mouseleave', function() {{ isDragging = false; }});
renderer.domElement.addEventListener('wheel', function(e) {{
    targetDistance = Math.max(5, Math.min(25, targetDistance + e.deltaY * 0.01));
}});

// ── Raycaster ──────────────────────────────────────────────────
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let hoveredSphere = null;

renderer.domElement.addEventListener('mousemove', function(e) {{
    mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    const visibleSpheres = allSpheres.filter(function(s) {{ return s.visible; }});
    const hits = raycaster.intersectObjects(visibleSpheres);
    const tooltip = document.getElementById('tooltip');

    if (hoveredSphere && (!hits.length || hits[0].object !== hoveredSphere)) {{
        hoveredSphere.material.emissiveIntensity = hoveredSphere.userData.baseEmissive;
        hoveredSphere.scale.set(1, 1, 1);
        hoveredSphere = null; tooltip.style.display = 'none';
        renderer.domElement.style.cursor = 'grab';
    }}
    if (hits.length && !isDragging) {{
        const s = hits[0].object;
        if (s !== hoveredSphere) {{
            hoveredSphere = s; s.material.emissiveIntensity = 0.5;
            s.scale.set(1.1, 1.1, 1.1); renderer.domElement.style.cursor = 'pointer';
        }}
        const d = s.userData;
        document.getElementById('tt-client').textContent = d.client;
        const iEl = document.getElementById('tt-industry'); iEl.textContent = d.industry; iEl.style.color = d.color;
        const pEl = document.getElementById('tt-platform'); pEl.textContent = d.platform; pEl.style.background = d.color+'22'; pEl.style.color = d.color;
        document.getElementById('tt-views').textContent = d.views.toLocaleString();
        document.getElementById('tt-engagement').textContent = d.engagement.toLocaleString();
        document.getElementById('tt-date').textContent = d.date;
        const tEl = document.getElementById('tt-tier'); tEl.textContent = d.emoji+' '+d.tier; tEl.style.color = d.color;
        tooltip.style.display = 'block';
        let tx = e.clientX + 20, ty = e.clientY - 20;
        if (tx + 240 > window.innerWidth) tx = e.clientX - 260;
        if (ty + 200 > window.innerHeight) ty = e.clientY - 200;
        tooltip.style.left = tx+'px'; tooltip.style.top = ty+'px';
    }}
}});

renderer.domElement.addEventListener('click', function() {{
    if (hoveredSphere) window.open(hoveredSphere.userData.url, '_blank');
}});

setTimeout(function() {{ document.getElementById('instructions').style.opacity = '0'; }}, 5000);

// ── Animation ─────────────────────────────────────────────────
const clock = new THREE.Clock();
function animate() {{
    requestAnimationFrame(animate);
    const t = clock.getElapsedTime();

    orbitAngle.x += (targetOrbit.x - orbitAngle.x) * 0.05;
    orbitAngle.y += (targetOrbit.y - orbitAngle.y) * 0.05;
    cameraDistance += (targetDistance - cameraDistance) * 0.05;
    camera.position.x = Math.sin(orbitAngle.x) * Math.cos(orbitAngle.y) * cameraDistance;
    camera.position.y = Math.sin(orbitAngle.y) * cameraDistance;
    camera.position.z = Math.cos(orbitAngle.x) * Math.cos(orbitAngle.y) * cameraDistance;
    camera.lookAt(0, 0, 0);
    if (!isDragging) targetOrbit.x += 0.001;

    allSpheres.forEach(function(s) {{
        if (!s.visible) return;
        s.position.y = s.userData.baseY + Math.sin(t * s.userData.bobSpeed + s.userData.bobOffset) * s.userData.bobAmount;
        s.rotation.y += 0.003; s.rotation.x += 0.001;
        if (s.userData.glowRing) s.userData.glowRing.lookAt(camera.position);
    }});

    foxEntries.forEach(function(fe) {{
        if (!fe.mesh.visible) return;
        fe.sprite.userData.orbitAngle += fe.sprite.userData.orbitSpeed * 0.02;
        fe.mesh.getWorldPosition(tmpVec);
        const r = fe.mesh.userData.radius * 1.08, a = fe.sprite.userData.orbitAngle;
        fe.sprite.position.x = tmpVec.x + Math.cos(a) * r;
        fe.sprite.position.z = tmpVec.z + Math.sin(a) * r;
        fe.sprite.position.y = tmpVec.y + Math.sin(a * 0.6) * r * 0.12;
    }});

    renderer.render(scene, camera);
}}
animate();

window.addEventListener('resize', function() {{
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
}});
</script>
</body>
</html>"""

    fn = f"viral_showcase_{ts}.html"
    with open(fn, "w") as f:
        f.write(html)
    print(f"3D showcase generated: {fn}")

    import subprocess
    subprocess.Popen(["open", fn])

    return fn


# ─── SLACK ──────────────────────────────────────────────────────

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

def send_slack_alerts(viral_videos):
    if not SLACK_WEBHOOK_URL:
        print("\nSlack alerts skipped (set SLACK_WEBHOOK_URL to enable)")
        return
    import requests
    for v in viral_videos:
        tier = v.get("tier_label", "VIRAL")
        emoji = {"VIRAL": "\U0001F525", "CASE STUDY": "\U0001F3AF",
                 "LEADERSHIP ALERT": "\U0001F680", "MEGA VIRAL": "\U0001F4A5"}.get(tier, "\U0001F525")
        payload = {
            "text": f"{emoji} {tier}: {v['client']}",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text",
                    "text": f"{emoji} {tier}: {v['client']}"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Client:*\n{v['client']}"},
                    {"type": "mrkdwn", "text": f"*Platform:*\n{v['platform']}"},
                    {"type": "mrkdwn", "text": f"*Views:*\n{v['views_total']:,}"},
                    {"type": "mrkdwn", "text": f"*Published:*\n{v['published_date']}"},
                    {"type": "mrkdwn", "text": f"*Posted by:*\n{v.get('posted_by', 'Unknown')}"},
                ]},
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"<{v['post_url']}|View Post> | Reach: {v['reach']:,} | Engagement: {v['engagement']:,}"}},
                {"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"Viral Tracker | {datetime.now().strftime('%Y-%m-%d %H:%M')} | #ClientWin"}]},
            ]
        }
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
        s = "sent" if resp.status_code == 200 else f"FAILED ({resp.status_code})"
        print(f"  Slack: {v['client']} ({v['platform']}): {s}")
        time.sleep(1)


def upload_files_to_slack(csv_fn, showcase_fn):
    """Upload the viral CSV and 3D showcase HTML to Slack so the team can access them."""
    if not SLACK_BOT_TOKEN:
        print("\nSlack file upload skipped (set SLACK_BOT_TOKEN to enable)")
        return
    channel = SLACK_CHANNEL_ID or SLACK_BOT_DM_CHANNEL

    import requests

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}

    for filepath, title in [(csv_fn, "Viral Videos CSV"), (showcase_fn, "3D Showcase")]:
        if not filepath:
            continue
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
                continue

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

    if cmd == "--discover-orgs":
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
                generate_report(viral, all_vids)
                send_slack_alerts(viral)                        # Post to Slack first
                fetch_thumbnails_from_slack(viral)              # Then grab thumbnails from Slack unfurls
                showcase_fn = generate_3d_showcase(viral)       # Then build 3D with thumbnails
                upload_files_to_slack(viral_csv_fn, showcase_fn)  # Upload CSV + showcase to Slack
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
  --help                Show this

Optional:
  export SLACK_WEBHOOK_URL='https://hooks.slack.com/...'
        """)
    else:
        print(f"Unknown: {cmd}. Try --help")

if __name__ == "__main__":
    main()
