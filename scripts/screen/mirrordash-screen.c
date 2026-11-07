// The mirror's screen: one fullscreen WPE WebKit view of the mirror's page, on labwc (WPEPlatform).
// It replaces Cog, which is built on the API WPE 2.54 deprecated and gets no releases past 0.18.
// A web process that dies, such as one out of GPU memory, ends this process with a failure, and
// systemd starts a new one: only a new one gets the GPU memory back.
// New here? README.md next to this file explains the chain, GLib's main loop and signals, and how to test a change.
#include <stdio.h>
#include <wpe/webkit.h>
#include <wpe/wpe-platform.h>

// The only place the view may go: the mirror's own server (its page's links and scripts can't send it elsewhere)
static const char *ORIGIN = "http://localhost:8000/";
// Waits for the server and then goes to ORIGIN. Shown as if it came from ORIGIN, so going there is no
// change of site: a change of site starts a new web process, and WPE 2.54 aborts the whole launcher when
// that happens while the screen is off (WPEViewWayland.cpp:601, RELEASE_ASSERT(!priv->frameCallback);
// like WebKit bug 298599). Measured on a Pi 3, 2026-11-06: 12 aborts in 12 starts from file://, none from ORIGIN.
static const char *LOADING_PAGE = "/home/pi/mirrordash/loading.html";

static void web_process_died(G_GNUC_UNUSED WebKitWebView *view, WebKitWebProcessTerminationReason reason, gpointer loop)
{
    fprintf(stderr, "The web process ended (%s); starting again gets its memory back\n",
            reason == WEBKIT_WEB_PROCESS_EXCEEDED_MEMORY_LIMIT ? "out of memory" : "crashed");
    g_main_loop_quit(loop);
}

static void view_closed(G_GNUC_UNUSED WebKitWebView *view, gpointer loop)
{
    fprintf(stderr, "The view was closed; starting again shows it again\n");
    g_main_loop_quit(loop);
}

// Navigations stay on ORIGIN; new windows (window.open, target=_blank) never open
static gboolean decide_policy(G_GNUC_UNUSED WebKitWebView *view, WebKitPolicyDecision *decision,
                              WebKitPolicyDecisionType type, G_GNUC_UNUSED gpointer data)
{
    if (type == WEBKIT_POLICY_DECISION_TYPE_RESPONSE)
        return FALSE; // WebKit's own choice: show the page, download nothing
    WebKitNavigationAction *action = webkit_navigation_policy_decision_get_navigation_action(WEBKIT_NAVIGATION_POLICY_DECISION(decision));
    const char *uri = webkit_uri_request_get_uri(webkit_navigation_action_get_request(action));
    if (type == WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION && g_str_has_prefix(uri, ORIGIN))
        return FALSE;
    fprintf(stderr, "Blocked a way off the mirror's page: %s\n", uri);
    webkit_policy_decision_ignore(decision);
    return TRUE;
}

int main(void)
{
    WPEDisplay *display = wpe_display_get_default(); // WPE_PLATFORM=wayland in the unit picks labwc's
    if (!display) {
        fprintf(stderr, "No display to show the mirror on\n");
        return 1;
    }
    GMainLoop *loop = g_main_loop_new(NULL, FALSE);
    // Started while the screen is off (wlr-randr --off takes its output away): wait for it. A view made
    // without a screen ties its frames to none, never draws, never shows, and stayed black once the
    // screen came on (Pi 3, 2026-11-06); turned off and on later, a running view carries on.
    if (wpe_display_get_n_screens(display) == 0) {
        fprintf(stderr, "Waiting for the screen to turn on\n");
        gulong waiting = g_signal_connect_swapped(display, "screen-added", G_CALLBACK(g_main_loop_quit), loop);
        g_main_loop_run(loop);
        g_signal_handler_disconnect(display, waiting);
    }
    WebKitWebView *view = WEBKIT_WEB_VIEW(g_object_new(WEBKIT_TYPE_WEB_VIEW, "display", display, NULL));
    WebKitColor black = { 0, 0, 0, 1 };
    webkit_web_view_set_background_color(view, &black);
    // The page's console (its errors too) in the journal: journalctl -u screen-kiosk
    webkit_settings_set_enable_write_console_messages_to_stdout(webkit_web_view_get_settings(view), TRUE);
    g_signal_connect(view, "web-process-terminated", G_CALLBACK(web_process_died), loop);
    g_signal_connect(view, "close", G_CALLBACK(view_closed), loop);
    g_signal_connect(view, "decide-policy", G_CALLBACK(decide_policy), NULL);
    WPEToplevel *toplevel = wpe_view_get_toplevel(webkit_web_view_get_wpe_view(view));
    if (toplevel)
        wpe_toplevel_fullscreen(toplevel);

    g_autofree char *loading = NULL;
    if (g_file_get_contents(LOADING_PAGE, &loading, NULL, NULL))
        webkit_web_view_load_html(view, loading, ORIGIN);
    else
        webkit_web_view_load_uri(view, ORIGIN); // black until the server answers; the app-watch restarts a silent page
    g_main_loop_run(loop);
    return 1; // the loop only ends when the web process or the view has
}
