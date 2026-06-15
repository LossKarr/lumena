import json, tempfile, collections
from pathlib import Path

merged = json.loads((Path(tempfile.gettempdir()) / "lumena_merged.json").read_text(encoding="utf-8"))
by = {r["name"]: r for r in merged}
mod = collections.defaultdict(list)
for r in merged:
    mod[r["module"] or "(runtime)"].append(r["name"])

RO = "—"  # tiret cadratin = lecture seule / hors-garde

HC = {
 "FILE": {"write_file","edit_file","apply_patch","insert_at_anchor","edit_by_lines","str_replace","multi_edit_file","create_file","create_html","create_markdown","create_from_template","create_email_html","create_ics","create_vcard","create_meeting_report","create_zip"},
 "DOC": {"create_pdf","create_docx","create_pptx","create_xlsx","create_csv","create_invoice_pdf","create_batch_documents","edit_docx","edit_pptx","edit_xlsx","annotate_pdf","add_watermark","assemble_document","convert_document"},
 "SITE": {"generate_website","serve_website","edit_website","write_website_files","create_project","delegate_task","delegate_task_bg"},
 "TASK": {"create_task","schedule_task","memory_save","memory_store","memory_add","create_skill"},
 "MAIL": {"mail_send","send_email","mail_reply_message"},
 "DISCORD": {"discord_send","discord_send_message","discord_send_embed","discord_create_channel","discord_create_category","discord_create_invite","discord_create_role","discord_delete_channel","discord_delete_message","discord_delete_role","discord_modify_channel","discord_pin","discord_unpin","discord_assign_role","discord_remove_role","discord_ban","discord_unban","discord_kick","discord_set_channel_permissions","discord_server_configure"},
 "MESSAGING": {"telegram_send_message","telegram_send_document","send_whatsapp_message","send_whatsapp_document","send_whatsapp_photo","send_whatsapp_audio","send_message","send_critical_sms"},
 "SOCIAL": {"twitter_post_tweet","twitter_reply","twitter_like","twitter_compose_thread"},
 "STRIPE": {"stripe_create_product","stripe_update_product","stripe_delete_product","stripe_create_price","stripe_create_payment_link","stripe_update_payment_link","stripe_create_customer","stripe_update_customer","stripe_create_subscription","stripe_cancel_subscription","stripe_create_invoice","stripe_send_invoice","stripe_void_invoice","stripe_add_invoice_item","stripe_create_checkout_session","stripe_create_coupon","stripe_delete_coupon","stripe_create_refund"},
 "GITHUB": {"github_repo_create","github_file_write","github_push_directory","git_add","git_commit","git_push_pull","git_init"},
 "IMAGE": {"generate_image","edit_image","generate_thumbnail","generate_thumbnail_pro","generate_logo","generate_svg","upscale_image","remove_background","replace_background","sketch_to_image","compose_image","generate_video","edit_video"},
 "NOTION": {"notion_create_page","notion_update_page","notion_add_to_database"},
 "TYPE": {"type_text","ui_type","paste","clear_field","browser_type","browser_type_index","browser_login","browser_save_login"},
 "OPEN_APP": {"open_app","open_url","open_application","browser_start","browser_navigate","run_command"},
 "CLICK": {"click","double_click","ui_click","mouse_pattern","click_element","find_element","browser_click","browser_click_index","browser_click_smart","browser_click_at","browser_navigate"},
 "LOGIN": {"browser_login","browser_verify","type_text","ui_type","paste","clear_field","browser_type","browser_type_index","browser_save_login"},
}
hc_of = {}
for fam, s in HC.items():
    for n in s:
        hc_of.setdefault(n, set()).add(fam)

F = {r["name"]: RO for r in merged}

def setf(names, fam):
    for n in names:
        if n in by:
            F[n] = fam

# agents
setf(["bg_start","bg_cancel","process_input","process_kill","process_run"], "EXEC")
setf(["delegate_task","delegate_task_bg"], "SITE")
# autonomy
setf(["cancel_task","delete_task","modify_task","remind","schedule_task"], "TASK")
# batch
setf(["apply_patches"], "FILE"); setf(["fanout_tasks"], "EXEC")
# browser
setf(["browser_type","browser_type_index","browser_frame_type"], "TYPE")
setf(["browser_click","browser_click_at","browser_click_index","browser_click_smart","browser_frame_click","browser_select","browser_hover","browser_drag","browser_drag_at","browser_keyboard_press","browser_scroll"], "CLICK")
setf(["browser_login","browser_save_login","browser_verify","browser_solve_challenge"], "LOGIN")
setf(["browser_start","browser_navigate","browser_new_tab","browser_open_tab","browser_back","browser_forward","browser_refresh","browser_switch_tab","browser_tab_switch","browser_stop","browser_close_tab","browser_close_all_tabs"], "OPEN_APP")
setf(["browser_save_pdf","browser_upload_file","browser_wait_for_download"], "FILE")
setf(["browser_evaluate","browser_frame_evaluate"], "EXEC")
setf(["browser_storage_set","browser_storage_clear","browser_cookies_clear","browser_network_clear","browser_block_resources","browser_unblock_resources","browser_emulate_device","browser_emulate_media","browser_set_geolocation","browser_handle_dialog","browser_accept_cookies","browser_dismiss_popups","browser_trace_start","browser_trace_stop","browser_batch"], "BROWSER_TECH")
# computer_use
setf(["type_text","ui_type"], "TYPE")
setf(["click","double_click","ui_click","mouse_pattern","click_element","find_element","drag","move_mouse","scroll","hotkey","press_key","set_screen","zoom","close_app","close_window"], "CLICK")
setf(["open_app","open_url","cursor_ide_local"], "OPEN_APP")
setf(["spotify_play"], "MEDIA")
setf(["computer_task"], "CU_TASK")
# config_manager
setf(["update_lumena_config"], "CONFIG")
# custom
setf(["custom_tool_create","custom_tool_load"], "SKILL")
# data_workbench
setf(["data_export"], "FILE")
# datagouv
setf(["datagouv_download_resource"], "FILE")
# discord_admin
for n in mod["discord_admin"]:
    if any(n.startswith(p) for p in ("discord_list","discord_fetch")) or n == "discord_server_info":
        F[n] = RO
    else:
        F[n] = "DISCORD"
# documents
doc_actions = ("create_","edit_","add_watermark","annotate_pdf","assemble_document","convert_document","batch_documents","fill_pdf_form","html_to_pdf","image_to_document","merge_pdfs","protect_pdf","save_template","sign_document","split_pdf","zip_documents","generate_chart")
for n in mod["documents"]:
    if n.startswith(doc_actions):
        F[n] = "DOC"
for n in ("analyze_document","compare_documents","list_pdf_fields","list_templates","read_document"):
    if n in by:
        F[n] = RO
# files
setf(["apply_patch","create_directory","create_zip","delete_file","edit_file","insert_at_anchor","multi_edit_file","undo_edit","write_file"], "FILE")
# git / github
setf(["git_add","git_commit","git_push_pull","git_init","git_branch","git_remote"], "GITHUB")
setf(["github_file_delete","github_file_write","github_issue_create","github_push_directory","github_repo_create","github_repo_delete"], "GITHUB")
# heartbeat
setf(["heartbeat_manage"], "CONFIG")
# http_api
setf(["http_request","http_upload_file","http_webhook_test","http_api_register"], "HTTP")
# ide
setf(["ide_editor_insert","ide_editor_save","ide_find_replace","ide_sidebar_create_file","ide_sidebar_create_folder","ide_sidebar_delete","ide_sidebar_rename","ide_write_file","ide_terminal","ide_terminal_clear","ide_open_file","ide_open_workspace","ide_navigate","ide_launch","ide_editor_close_tab","ide_editor_switch_tab","ide_editor_cursor_goto","ide_editor_select","ide_window_close","ide_window_maximize","ide_window_minimize","ide_toggle_chat","ide_toggle_search","ide_toggle_sidebar","ide_toggle_terminal","ide_diff"], "IDE")
# image_gen
setf(["compose_image","edit_image","generate_headlines","generate_image","generate_logo","generate_svg","generate_thumbnail","generate_thumbnail_pro","remove_background","replace_background","sketch_to_image","upscale_image"], "IMAGE")
# ionos
setf(["deploy_to_ionos","ionos_add_site","ionos_remove_site","ionos_delete_files","update_ionos_files","ionos_set_site_database","ionos_clear_site_database"], "DEPLOY")
for n in mod["ionos"]:
    if n.startswith("ionos_db"):
        if any(k in n for k in ("get","list","describe","select","status","test")):
            F[n] = RO
        elif "set_" in n or n.endswith("_config"):
            F[n] = "DB_CONFIG"
        elif "propose" in n:
            F[n] = "DB_PROPOSE"
        else:
            F[n] = "DB"
setf(["ionos_test_site_database"], RO)
# mail
setf(["mail_send","mail_reply_message"], "MAIL")
setf(["mail_account_upsert","mail_remove_account","mail_delete_message","mail_move_message","mail_download_attachments"], "MAIL_ADMIN")
setf(["notify_critical","place_critical_call","send_critical_sms","send_whatsapp_audio","send_whatsapp_document","send_whatsapp_message","send_whatsapp_photo","telegram_send_document"], "MESSAGING")
# memory
setf(["memory_add","write_journal","learn_from_action"], "TASK")
# n8n
for n in mod["n8n"]:
    if n.startswith("n8n_list") or n in ("n8n_status","n8n_get_execution","n8n_get_workflow"):
        F[n] = RO
    else:
        F[n] = "N8N"
# network
setf(["network_exec","network_file_download","network_file_edit","network_file_upload","network_self_deploy","network_set_credentials","network_shutdown","network_wol"], "NETWORK")
# notion
setf(["notion_create_page","notion_update_page","notion_add_to_database"], "NOTION")
# peer
setf(["delegate_to_peer","propose_peer_knowledge","orchestrate_peer_request","peer_team_request","run_peer_task_sync","submit_peer_task"], "PEER")
# perception
setf(["ingest_document"], "MEMORY")
# plans
setf(["plan_create","plan_update","plan_done"], "TASK")
# project
setf(["dev_run_fix","edit_notebook_cell","lint_and_fix","test_and_fix"], "EXEC")
setf(["create_project"], "SITE")
# remotion
setf(["edit_video","generate_video","preview_video"], "IMAGE")
# security
setf(["capture_traffic","execute_multilang","multi_agent_parallel","netcat_probe","nmap_scan","port_scan_fast","reverse_shell_listen","ssh_exec"], "EXEC")
# skills
setf(["create_skill","edit_own_code","custom_tool_create"], "SKILL")
setf(["execute_skill","reload_skills","rollback","sync_skills_main","run_tests"], "EXEC")
# spotify
setf(["spotify_api_play","spotify_next","spotify_pause","spotify_prev","spotify_queue","spotify_resume","spotify_volume"], "MEDIA")
# stripe
for n in mod["stripe_api"]:
    if n.startswith(("stripe_list","stripe_get","stripe_search")) or n == "stripe_cli_status":
        F[n] = RO
    elif n.startswith("stripe_cli"):
        F[n] = "EXEC"
    else:
        F[n] = "STRIPE"
# system
setf(["run_command","parallel_tools"], "EXEC")
# twitter
setf(["twitter_post_tweet","twitter_reply","twitter_like","twitter_compose_thread"], "SOCIAL")
# web
setf(["web_crawl_campaign","web_crawl_campaign_export","web_crawl_campaign_pro_report"], "FILE")
# website
setf(["edit_website","export_website_zip","generate_website","serve_website","stop_website_server","write_website_files"], "SITE")

out = collections.defaultdict(list)
for r in merged:
    out[F[r["name"]]].append(r["name"])
print("Familles utilisees:")
for fam, names in sorted(out.items(), key=lambda x: (x[0] == RO, -len(x[1]))):
    print(f"  {fam:14s} {len(names)}")
Path(tempfile.gettempdir(), "lumena_fam.json").write_text(json.dumps(F, ensure_ascii=False), encoding="utf-8")
Path(tempfile.gettempdir(), "lumena_hcof.json").write_text(json.dumps({k: sorted(v) for k, v in hc_of.items()}, ensure_ascii=False), encoding="utf-8")
print("total:", len(F))
