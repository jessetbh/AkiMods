// Camera: keyboard + mouse free camera.
//
// F6 toggles a free-flying camera: the mouse looks around, the wheel moves in and out along the
// view, W/S move up and down relative to the camera, A/D sideways, Space / Ctrl straight up and
// down, Shift moves faster. It starts from the game's current view, and turning
// it off hands the view back to the game. While it's on, the game's keyboard bindings are
// suppressed (controllers still work) and the cursor is locked to the window; menus get the
// cursor back.
//
// No Mercy, WM2000 and VPW2 build their camera the same way (see README.md):
//   guPerspectiveF(proj, ...);  guLookAtF(view, eye..., at..., up = 0,1,0);
//   CAMERA_FUNC(gfx, proj, view)  ->  CAMERA_MATRIX = view x proj   (row-vector convention)
// and every model is then drawn with model x CAMERA_MATRIX. This mod records proj/view on entry
// to CAMERA_FUNC and, with the free camera on, rebuilds the matrix on return from our own eye
// and angles: CAMERA_MATRIX = look_at(eye, eye + forward) x proj.

#include "aki_mod.h"
#include "recomp_api.h"
#include "recomp_input.h"

#if !defined(CAMERA_FUNC) || !defined(CAMERA_MATRIX)
#error "CAMERA_FUNC and CAMERA_MATRIX must be defined per game (mod.psd1)"
#endif

#define PI 3.14159265f

// SDL scancodes.
#define KEY_A 4
#define KEY_D 7
#define KEY_S 22
#define KEY_W 26
#define KEY_F6 63
#define KEY_SPACE 44
#define KEY_LCTRL 224
#define KEY_LSHIFT 225
#define KEY_RCTRL 228
#define KEY_RSHIFT 229

#define MOVE_SPEED 6.0f         // game units per frame (ring posts sit at about +-230)
#define FAST_MULTIPLIER 4.0f    // with Shift held
#define LOOK_SPEED 0.0025f      // radians per mouse pixel at 100% sensitivity
#define PITCH_LIMIT 1.5f        // radians (~86 degrees) up or down
#define WHEEL_STEP 40.0f        // game units per wheel click (x move speed, x4 with Shift)
#define WHEEL_GLIDE 0.25f       // share of the remaining wheel distance covered each frame

typedef float Mtx4[4][4];

static float* saved_proj;
static float* saved_view;

// Free camera state.
static u32 active;
static u32 toggle_was_down;
static u32 needs_capture;   // copy the game's eye and angles on the next camera build
static float eye[3];
static float yaw;           // forward = (sin yaw cos pitch, sin pitch, cos yaw cos pitch)
static float pitch;
static float dolly;         // wheel distance still to glide along the view (+ = in)

static void mtx_identity(Mtx4 m) {
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j++) {
            m[i][j] = (i == j) ? 1.0f : 0.0f;
        }
    }
}

// out = a x b (out may not alias a or b).
static void mtx_mul(Mtx4 out, Mtx4 a, Mtx4 b) {
    for (int i = 0; i < 4; i++) {
        for (int j = 0; j < 4; j++) {
            out[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] + a[i][3] * b[3][j];
        }
    }
}

static void mtx_load(Mtx4 out, const float* src) {
    for (int i = 0; i < 16; i++) {
        out[i / 4][i % 4] = src[i];
    }
}

// --- Freestanding math (no libm in mods) ---

static float wrap_pi(float x) {
    while (x > PI) x -= 2.0f * PI;
    while (x < -PI) x += 2.0f * PI;
    return x;
}

// Taylor series on [-pi/2, pi/2] (error < 1e-7), using sin(x) = sin(pi - x) outside it.
static float sin_rad(float x) {
    x = wrap_pi(x);
    if (x > PI * 0.5f) x = PI - x;
    else if (x < -PI * 0.5f) x = -PI - x;
    float x2 = x * x;
    return x * (1.0f - x2 / 6.0f * (1.0f - x2 / 20.0f * (1.0f - x2 / 42.0f * (1.0f - x2 / 72.0f * (1.0f - x2 / 110.0f)))));
}

static float cos_rad(float x) {
    return sin_rad(x + PI * 0.5f);
}

static float sqrt_f(float x) {
    if (x <= 0.0f) return 0.0f;
    float r = x > 1.0f ? x * 0.5f : 1.0f;
    for (int i = 0; i < 20; i++) {
        r = 0.5f * (r + x / r);
    }
    return r;
}

// atan2 via a minimax atan polynomial on [0, 1] (max error ~1e-5 rad).
static float atan2_f(float y, float x) {
    const float ax = x < 0.0f ? -x : x, ay = y < 0.0f ? -y : y;
    if (ax == 0.0f && ay == 0.0f) return 0.0f;
    const float z = ax > ay ? ay / ax : ax / ay;
    const float z2 = z * z;
    float a = z * (0.9998660f + z2 * (-0.3302995f + z2 * (0.1801410f + z2 * (-0.0851330f + z2 * 0.0208351f))));
    if (ay > ax) a = PI * 0.5f - a;
    if (x < 0.0f) a = PI - a;
    return y < 0.0f ? -a : a;
}

// --- Camera ---

static void forward_vector(float f[3]) {
    const float cp = cos_rad(pitch);
    f[0] = sin_rad(yaw) * cp;
    f[1] = sin_rad(pitch);
    f[2] = cos_rad(yaw) * cp;
}

// Movement axes: forward along the view, right level with the ground, up = the view's
// screen-up (right x forward, tilts with the pitch).
static void move_axes(float f[3], float r[3], float u[3]) {
    forward_vector(f);
    r[0] = -cos_rad(yaw);
    r[1] = 0.0f;
    r[2] = sin_rad(yaw);
    u[0] = r[1] * f[2] - r[2] * f[1];
    u[1] = r[2] * f[0] - r[0] * f[2];
    u[2] = r[0] * f[1] - r[1] * f[0];
}

// Game camera position and forward direction from its view (v_cam = v_world x R + t):
// C = -t x R^T, forward F = (0,0,-1) x R^T = -(column 2 of R).
static void game_camera(Mtx4 view, float c[3], float f[3]) {
    for (int k = 0; k < 3; k++) {
        c[k] = -(view[3][0] * view[k][0] + view[3][1] * view[k][1] + view[3][2] * view[k][2]);
        f[k] = -view[k][2];
    }
}

// Look-at view in the same convention as the game's guLookAtF (up = +y): columns are the
// camera's right, up and backward axes; row 3 the translation.
static void look_at(Mtx4 out, const float from[3], const float at[3]) {
    float f[3] = { at[0] - from[0], at[1] - from[1], at[2] - from[2] };
    float len = sqrt_f(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]);
    for (int k = 0; k < 3; k++) f[k] /= len;
    float r[3] = { -f[2], 0.0f, f[0] }; // f x (0,1,0)
    len = sqrt_f(r[0] * r[0] + r[2] * r[2]);
    r[0] /= len;
    r[2] /= len;
    const float u[3] = { r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0] }; // r x f
    mtx_identity(out);
    for (int k = 0; k < 3; k++) {
        out[k][0] = r[k];
        out[k][1] = u[k];
        out[k][2] = -f[k];
    }
    out[3][0] = -(from[0] * r[0] + from[1] * r[1] + from[2] * r[2]);
    out[3][1] = -(from[0] * u[0] + from[1] * u[1] + from[2] * u[2]);
    out[3][2] = (from[0] * f[0] + from[1] * f[1] + from[2] * f[2]);
}

// Starts the free camera from the game's view: same eye, same direction.
static void capture_game_view(Mtx4 view) {
    float f[3];
    game_camera(view, eye, f);
    yaw = atan2_f(f[0], f[2]);
    pitch = atan2_f(f[1], sqrt_f(f[0] * f[0] + f[2] * f[2]));
}

// The free camera's view: from eye along yaw/pitch.
static void build_free_view(Mtx4 view) {
    float f[3];
    forward_vector(f);
    const float at[3] = { eye[0] + f[0], eye[1] + f[1], eye[2] + f[2] };
    look_at(view, eye, at);
}

static void set_active(u32 on) {
    active = on;
    recomp_set_mouse_captured(on);
    recomp_set_keyboard_suppressed(on);
    recomp_take_mouse_motion(); // drop whatever moved before
    if (on) {
        needs_capture = 1;
        dolly = 0.0f;
    }
}

// Keyboard and mouse, once per frame.
AKI_ON_FRAME void camera_on_frame(void) {
    const u32 toggle = recomp_get_key_down(KEY_F6);
    if (toggle && !toggle_was_down) {
        set_active(!active);
    }
    toggle_was_down = toggle;
    if (!active) {
        return;
    }

    recomp_take_mouse_motion();
    if (needs_capture) {
        return; // the next camera build copies the game's view first
    }

    // Look.
    const float sensitivity = LOOK_SPEED * (float)recomp_get_config_double("mouse_sensitivity") / 100.0f;
    const float invert = recomp_get_config_u32("invert_y") ? -1.0f : 1.0f;
    yaw = wrap_pi(yaw - recomp_get_mouse_dx() * sensitivity);
    pitch -= recomp_get_mouse_dy() * sensitivity * invert;
    if (pitch > PITCH_LIMIT) pitch = PITCH_LIMIT;
    if (pitch < -PITCH_LIMIT) pitch = -PITCH_LIMIT;

    // Move: W/S up and down relative to the camera, A/D sideways, Space/Ctrl straight up/down.
    float f[3], r[3], u[3];
    move_axes(f, r, u);
    const float lift = (float)recomp_get_key_down(KEY_W) - (float)recomp_get_key_down(KEY_S);
    const float strafe = (float)recomp_get_key_down(KEY_D) - (float)recomp_get_key_down(KEY_A);
    const float rise = (float)recomp_get_key_down(KEY_SPACE) -
        (float)(recomp_get_key_down(KEY_LCTRL) | recomp_get_key_down(KEY_RCTRL));
    float speed = MOVE_SPEED * (float)recomp_get_config_double("move_speed") / 100.0f;
    if (recomp_get_key_down(KEY_LSHIFT) | recomp_get_key_down(KEY_RSHIFT)) {
        speed *= FAST_MULTIPLIER;
    }

    // Wheel: in and out along the view, glided over a few frames instead of jumping.
    dolly += (float)recomp_get_mouse_wheel() * WHEEL_STEP * speed / MOVE_SPEED;
    const float step = dolly * WHEEL_GLIDE;
    dolly -= step;
    if (dolly > -0.5f && dolly < 0.5f) dolly = 0.0f;

    for (int k = 0; k < 3; k++) {
        eye[k] += (u[k] * lift + r[k] * strafe) * speed + f[k] * step;
    }
    eye[1] += rise * speed;
}

// --- Camera hooks ---

RECOMP_HOOK(AKI_STRINGIFY(CAMERA_FUNC)) void camera_on_build(u32 gfx, float* proj, float* view) {
    saved_proj = proj;
    saved_view = view;
}

RECOMP_HOOK_RETURN(AKI_STRINGIFY(CAMERA_FUNC)) void camera_on_built(void) {
    if (!active || saved_proj == 0 || saved_view == 0) {
        return; // the game's own camera
    }

    Mtx4 view, proj, a;
    mtx_load(view, saved_view);
    mtx_load(proj, saved_proj);
    if (needs_capture) {
        capture_game_view(view);
        needs_capture = 0;
    }

    build_free_view(view);
    mtx_mul(a, view, proj);

    float* out = (float*)CAMERA_MATRIX;
    for (int i = 0; i < 16; i++) {
        out[i] = a[i / 4][i % 4];
    }
}
