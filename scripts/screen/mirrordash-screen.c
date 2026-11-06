// The mirror's screen: one fullscreen WPE WebKit view of the start page, on labwc (WPEPlatform).
// It replaces Cog, which is built on the API WPE 2.54 deprecated and gets no releases past 0.18.
// A web process that dies, such as one out of GPU memory, ends this process with a failure, and
// systemd starts a new one: only a new one gets the GPU memory back.
#include <stdio.h>
#include <wpe/webkit.h>
#include <wpe/wpe-platform.h>

static const char *DEFAULT_PAGE = "file:///home/pi/mirrordash/loading.html";

static void web_process_died(WebKitWebView *view, WebKitWebProcessTerminationReason reason, gpointer loop)
{
    fprintf(stderr, "The web process ended (%s); starting again gets its memory back\n",
            reason == WEBKIT_WEB_PROCESS_EXCEEDED_MEMORY_LIMIT ? "out of memory" : "crashed");
    g_main_loop_quit(loop);
}

int main(int argc, char *argv[])
{
    WPEDisplay *display = wpe_display_get_default(); // WPE_PLATFORM=wayland in the unit picks labwc's
    if (!display) {
        fprintf(stderr, "No display to show the mirror on\n");
        return 1;
    }
    GMainLoop *loop = g_main_loop_new(NULL, FALSE);
    WebKitWebView *view = WEBKIT_WEB_VIEW(g_object_new(WEBKIT_TYPE_WEB_VIEW, "display", display, NULL));
    WebKitColor black = { 0, 0, 0, 1 };
    webkit_web_view_set_background_color(view, &black);
    g_signal_connect(view, "web-process-terminated", G_CALLBACK(web_process_died), loop);
    WPEToplevel *toplevel = wpe_view_get_toplevel(webkit_web_view_get_wpe_view(view));
    if (toplevel)
        wpe_toplevel_fullscreen(toplevel);
    webkit_web_view_load_uri(view, argc > 1 ? argv[1] : DEFAULT_PAGE);
    g_main_loop_run(loop);
    return 1; // the loop only ends when the web process has
}
