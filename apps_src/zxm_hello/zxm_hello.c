#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

static void draw_cb(Canvas* c, void* ctx) {
    UNUSED(ctx);
    canvas_clear(c);
    canvas_set_font(c, FontPrimary);
    canvas_draw_str(c, 8, 16, "ZxmWare Hello");
    canvas_set_font(c, FontSecondary);
    canvas_draw_str(c, 8, 34, "Built on-demand!");
    canvas_draw_str(c, 8, 50, "Press Back to exit");
}

static void input_cb(InputEvent* e, void* ctx) {
    FuriMessageQueue* q = ctx;
    furi_message_queue_put(q, e, FuriWaitForever);
}

int32_t zxm_hello_app(void* p) {
    UNUSED(p);
    FuriMessageQueue* q = furi_message_queue_alloc(8, sizeof(InputEvent));
    ViewPort* vp = view_port_alloc();
    view_port_draw_callback_set(vp, draw_cb, NULL);
    view_port_input_callback_set(vp, input_cb, q);
    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, vp, GuiLayerFullscreen);

    InputEvent e;
    while(furi_message_queue_get(q, &e, FuriWaitForever) == FuriStatusOk) {
        if(e.type == InputTypeShort && e.key == InputKeyBack) break;
    }

    gui_remove_view_port(gui, vp);
    furi_record_close(RECORD_GUI);
    view_port_free(vp);
    furi_message_queue_free(q);
    return 0;
}
