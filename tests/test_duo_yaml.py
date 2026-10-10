"""The flow and agent definitions in .gitlab/duo must only reference tool ids that exist in the
GitLab Duo Agent Platform catalog (checked against `aiCatalogBuiltInTools` in the hackathon project on
2026-10-10 and the docs' agent tools page). Keeps a schema-rejected name from sneaking back in."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KNOWN_TOOLS = set(
    """
    add_merge_request_reviewers build_review_merge_request_context create_merge_request create_merge_request_diff_note
    create_merge_request_note get_merge_request list_all_merge_request_notes list_merge_request_diffs
    list_mr_discussions
    reply_to_discussion set_discussion_resolved submit_mr_review update_merge_request list_merge_requests
    create_issue create_issue_note get_issue get_issue_note list_issue_notes list_issues update_issue create_work_item
    create_work_item_note get_work_item get_work_item_notes get_work_item_statuses list_work_items update_work_item
    create_epic get_epic get_epic_note list_epic_notes list_epics update_epic
    ci_linter get_downstream_pipelines get_failing_bridge_jobs get_job_logs get_pipeline_errors
    get_pipeline_failing_jobs
    run_tests get_pipeline list_pipelines list_releases
    create_branch create_commit get_commit get_commit_comments get_commit_diff get_project get_repository_file
    get_repository_files list_commits list_repository_tree
    confirm_vulnerability create_vulnerability_issue dismiss_vulnerability get_security_finding_details
    get_vulnerability_details link_vulnerability_to_issue link_vulnerability_to_merge_request list_security_findings
    list_vulnerabilities post_sast_fp_analysis_to_gitlab revert_to_detected_vulnerability update_vulnerability_severity
    add_new_task create_plan get_plan remove_task set_task_status update_task_description
    gitlab_blob_search gitlab_commit_search gitlab_documentation_search gitlab_group_project_search gitlab_issue_search
    gitlab_merge_request_search gitlab_milestone_search gitlab_note_search gitlab__user_search gitlab_wiki_blob_search
    run_glql_query list_group_audit_events list_instance_audit_events list_project_audit_events
    get_current_user get_session_context get_wiki_page extract_lines_from_text
    create_file_with_contents edit_file find_files grep list_dir mkdir read_file read_files run_command notify_me_when
    run_git_command start_flow
    """.split()
)
REMOVED_IDS = {"gitlab_api_get", "gitlab_graphql", "search_code"}  # not in the live catalog


def _flows() -> list[Path]:
    return sorted((ROOT / ".gitlab" / "duo" / "flows").glob("*.yaml"))


def _agents() -> list[Path]:
    return sorted((ROOT / ".gitlab" / "duo" / "agents").glob("*.yaml"))


def test_flows_have_required_keys_known_tools_and_consistent_routing():
    assert len(_flows()) == 5
    for f in _flows():
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for key in ("version", "environment", "components", "prompts", "routers", "flow"):
            assert key in doc, f"{f.name}: missing {key}"
        assert doc["version"] == "v1" and doc["environment"] == "ambient"
        names = {c["name"] for c in doc["components"]}
        assert doc["flow"]["entry_point"] in names
        prompt_ids = {p["prompt_id"] for p in doc["prompts"]}
        for c in doc["components"]:
            unknown = sorted(set(c.get("toolset", [])) - KNOWN_TOOLS)
            assert not unknown, f"{f.name}/{c['name']}: unknown tools {unknown}"
            assert c["prompt_id"] in prompt_ids, f"{f.name}/{c['name']}: prompt {c['prompt_id']} not defined"
        for r in doc["routers"]:
            assert r["from"] in names
            if "to" in r:
                targets = [r["to"]]
            else:
                # flow registry v1: the fallback lives INSIDE routes as the "default_route" key
                # (server rejects it as a sibling of routes: "disallowed additional property")
                assert "default_route" not in r["condition"], f"{f.name}: default_route must be inside routes"
                assert "default_route" in r["condition"]["routes"], f"{f.name}: routes needs default_route"
                targets = list(r["condition"]["routes"].values())
            for t in targets:
                assert t in names or t in ("end", "abort"), f"{f.name}: router target {t} unknown"
        text = f.read_text(encoding="utf-8")
        for bad in REMOVED_IDS:
            assert f"- {bad}" not in text, f"{f.name}: references removed tool {bad}"


def test_agents_reference_known_tools_and_no_custom_mcp():
    assert len(_agents()) == 4
    for f in _agents():
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert doc["display_name"] and doc["system_prompt"].strip()
        unknown = sorted(set(doc["tools"]) - KNOWN_TOOLS)
        assert not unknown, f"{f.name}: unknown tools {unknown}"
        assert "mcp_servers" not in doc, f"{f.name}: custom MCP servers cannot be attached on GitLab.com"
