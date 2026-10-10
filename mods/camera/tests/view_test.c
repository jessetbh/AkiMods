// Host-side test of the free camera math (no game needed): capturing the game's camera and
// rebuilding it must reproduce the game's view (so F6 starts without a jump); the wheel must move
// along the view, W toward the screen's top, D to the screen's right, and turning the mouse right
// must turn the view right.
// Build + run: .\mods\camera\tests\run.ps1
#include <stdio.h>
#include <math.h>

#define AKI_FRAME_HOOK func_test
#define CAMERA_FUNC func_cam
#define CAMERA_MATRIX 0
#include "../src/camera.c"

static int failures;
static void check(const char* name, int ok) {
    printf("%s %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) failures++;
}

// Point p in view space (row vector x view).
static void to_view(Mtx4 v, const float p[3], float out[3]) {
    for (int j = 0; j < 3; j++) {
        out[j] = p[0] * v[0][j] + p[1] * v[1][j] + p[2] * v[2][j] + v[3][j];
    }
}

int main(void) {
    // No Mercy's match view, read from a running game (rows; row 3 = translation).
    Mtx4 game = {
        { -1.0f, 0.0f, 0.0f, 0.0f },
        { 0.0f, 0.943478f, 0.331435f, 0.0f },
        { 0.0f, 0.331435f, -0.943478f, 0.0f },
        { 6.49013f, -162.621f, -1134.01f, 1.0f },
    };
    Mtx4 v;
    float worst = 0.0f;

    capture_game_view(game);
    build_free_view(v);
    float worst_t = 0.0f;
    for (int i = 0; i < 12; i++) worst = fmaxf(worst, fabsf(v[i / 4][i % 4] - game[i / 4][i % 4]));
    for (int i = 12; i < 15; i++) worst_t = fmaxf(worst_t, fabsf(v[i / 4][i % 4] - game[i / 4][i % 4]));
    printf("  max |rebuilt - game|: rotation %g, translation %g (yaw %.3f pitch %.3f eye %.1f %.1f %.1f)\n", worst, worst_t, yaw, pitch, eye[0], eye[1], eye[2]);
    // Translations are ~1000 units: 0.05 is a relative error of 5e-5 (float rounding).
    check("capture + rebuild reproduces the game view", worst < 1e-3f && worst_t < 0.05f);

    // A point 500 units ahead stays centred when the eye moves forward along the view.
    float f[3], ahead[3], pv[3];
    forward_vector(f);
    for (int k = 0; k < 3; k++) ahead[k] = eye[k] + f[k] * 500.0f;
    for (int k = 0; k < 3; k++) eye[k] += f[k] * 100.0f;
    build_free_view(v);
    to_view(v, ahead, pv);
    check("wheel moves along the view (point ahead stays centred, 100 closer)", fabsf(pv[0]) < 0.5f && fabsf(pv[1]) < 0.5f && fabsf(-pv[2] - 400.0f) < 1.0f);

    // D: the level right vector is the view's screen-right (+x in view space).
    float mf[3], r[3], u[3];
    move_axes(mf, r, u);
    const float right_point[3] = { eye[0] + r[0] * 100.0f, eye[1], eye[2] + r[2] * 100.0f };
    to_view(v, right_point, pv);
    check("D moves toward screen right", pv[0] > 90.0f);

    // W: the up vector is the view's screen-up (+y in view space), even with the view pitched.
    const float up_point[3] = { eye[0] + u[0] * 100.0f, eye[1] + u[1] * 100.0f, eye[2] + u[2] * 100.0f };
    to_view(v, up_point, pv);
    printf("  pitch %.3f, up point in view space: (%.2f, %.2f, %.2f)\n", pitch, pv[0], pv[1], pv[2]);
    check("W moves straight up the screen", fabsf(pv[0]) < 0.5f && fabsf(pv[1] - 100.0f) < 0.5f && fabsf(pv[2]) < 0.5f);

    // Mouse right (yaw decreases): a point on the screen's right moves toward the centre.
    const float point[3] = { ahead[0] + r[0] * 100.0f, ahead[1], ahead[2] + r[2] * 100.0f };
    to_view(v, point, pv);
    const float before = pv[0];
    yaw -= 0.1f;
    build_free_view(v);
    to_view(v, point, pv);
    printf("  right-hand point x: %.1f -> %.1f after turning right\n", before, pv[0]);
    check("mouse right turns the view right", before > 0.0f && pv[0] < before);

    check("atan2 quadrants", fabsf(atan2_f(1, -1) - atan2f(1, -1)) < 1e-4f && fabsf(atan2_f(-1, -1) - atan2f(-1, -1)) < 1e-4f);
    check("sqrt", fabsf(sqrt_f(1134.0f * 1134.0f) - 1134.0f) < 1e-2f);

    printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures != 0;
}
