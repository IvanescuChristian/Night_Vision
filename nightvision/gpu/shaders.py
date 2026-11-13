# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""GLSL 330 core sources for the enhancement passes."""
from ..core.curves import bayer_glsl

# One oversized triangle covers the viewport; no vertex buffers needed.
VERTEX = """#version 330 core
out vec2 uv;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

# Pass 1: tone curve. The LUT comes from the CPU build_float_lut(): 256
# unrounded float values (R32F texture), so the curve is not quantised to 8 bit
# here. The result goes to a half-float target and is only quantised (with
# dither) by the final pass.
TONE = """#version 330 core
uniform sampler2D src;
uniform sampler2D lut;
in vec2 uv;
layout(location = 0) out vec4 o;
void main() {
    vec4 c = texelFetch(src, ivec2(gl_FragCoord.xy), 0);
    ivec3 i = clamp(ivec3(c.rgb * 255.0 + 0.5), ivec3(0), ivec3(255));
    o = vec4(texelFetch(lut, ivec2(i.r, 0), 0).r,
             texelFetch(lut, ivec2(i.g, 0), 0).r,
             texelFetch(lut, ivec2(i.b, 0), 0).r,
             c.a);
}
"""

# Pass 2: luma downsample storing (Y, Y^2) so a later blur yields local mean
# and variance. `o` is the source-texel offset of the 4 bilinear taps: 1.0
# for 4x reduction from full res (each tap averages 2x2), 0.0 for 2x from an
# already reduced RG texture (a single tap on the 2x2 corner averages it).
DOWN = """#version 330 core
uniform sampler2D src;
uniform vec2 texel;
uniform float off;
uniform bool from_color;
in vec2 uv;
layout(location = 0) out vec4 o;
vec2 stat(vec2 p) {
    vec4 c = textureLod(src, p, 0.0);
    if (from_color) {
        float y = dot(c.bgr, vec3(0.299, 0.587, 0.114));
        return vec2(y, y * y);
    }
    return c.rg;
}
void main() {
    vec2 d = texel * off;
    vec2 a = stat(uv + vec2(-d.x, -d.y)) + stat(uv + vec2(d.x, -d.y))
           + stat(uv + vec2(-d.x,  d.y)) + stat(uv + vec2(d.x,  d.y));
    o = vec4(a * 0.25, 0.0, 0.0);
}
"""

# Separable Gaussian; weights computed in-shader so sigma is a uniform.
# from_color: read luma of an RGB texture instead of the stored RG stats
# (used for the full-res sharpen blur, where no reduced copy exists).
# Loop bound is a compile-time constant (some drivers unroll or reject
# uniform-bounded loops); `radius` only exits early. Taps are mirrored so the
# weight is computed once per pair.
BLUR_MAX_RADIUS = 36

BLUR = """#version 330 core
uniform sampler2D src;
uniform vec2 stepv;
uniform float sigma;
uniform int radius;
uniform bool from_color;
in vec2 uv;
layout(location = 0) out vec4 o;
const int MAX_RADIUS = @MAX_RADIUS@;
vec2 tap(vec2 p) {
    vec4 c = textureLod(src, p, 0.0);
    return from_color ? vec2(dot(c.bgr, vec3(0.299, 0.587, 0.114)), 0.0) : c.rg;
}
void main() {
    float k = -0.5 / (sigma * sigma);
    vec2 acc = tap(uv);
    float ws = 1.0;
    for (int i = 1; i <= MAX_RADIUS; ++i) {
        if (i > radius) {
            break;
        }
        float w = exp(k * float(i * i));
        acc += (tap(uv + stepv * float(i)) + tap(uv - stepv * float(i))) * w;
        ws += 2.0 * w;
    }
    o = vec4(acc / ws, 0.0, 0.0);
}
""".replace("@MAX_RADIUS@", str(BLUR_MAX_RADIUS))

# Final pass: local contrast proxy, clarity, sharpen, saturation, all in a
# luma/chroma split (Y shifted, chroma kept) so hue is preserved.
COMPOSE = """#version 330 core
uniform sampler2D tone;        // full-res tone-mapped frame
uniform sampler2D lc_tex;      // blurred (mean, mean of squares), 1/8 res
uniform sampler2D clarity_tex; // blurred luma, 1/4 res (clarity)
uniform sampler2D sharpen_tex; // blurred luma, full res (sharpen)
uniform float lc_amount;
uniform float lc_gain_max;
uniform float lc_target_sd;
uniform float clarity_amount;
uniform float sharpen_amount;
uniform float saturation;
in vec2 uv;
layout(location = 0) out vec4 o;
// 8x8 Bayer matrix, row-major [y][x]; same table as core.curves.BAYER8.
const int BAYER[64] = int[64](@BAYER@);
void main() {
    vec4 c = texelFetch(tone, ivec2(gl_FragCoord.xy), 0);
    vec3 rgb = c.bgr;
    float y0 = dot(rgb, vec3(0.299, 0.587, 0.114));
    float y = y0;
    if (lc_amount > 0.0) {
        vec2 s = textureLod(lc_tex, uv, 0.0).rg;
        float sd = sqrt(max(s.y - s.x * s.x, 0.0));
        float g = clamp(lc_target_sd / (sd + 0.01), 1.0, lc_gain_max);
        y = mix(y, s.x + (y - s.x) * g, lc_amount);
    }
    if (clarity_amount > 0.0) {
        y += clarity_amount * (y - textureLod(clarity_tex, uv, 0.0).r);
    }
    if (sharpen_amount > 0.0) {
        y += sharpen_amount * (y0 - texelFetch(sharpen_tex, ivec2(gl_FragCoord.xy), 0).r);
    }
    vec3 chroma = (rgb - y0) * saturation;
    // Static ordered dither before the 8-bit store: code = floor(v*255 + t),
    // t in (0,1) from the pixel position (mean 0.5, so unbiased, no flicker).
    ivec2 fc = ivec2(gl_FragCoord.xy);
    float t = (float(BAYER[(fc.x & 7) + (fc.y & 7) * 8]) + 0.5) / 64.0;
    vec3 v = floor(clamp(vec3(y) + chroma, 0.0, 1.0) * 255.0 + t) / 255.0;
    o = vec4(v.bgr, c.a);
}
""".replace("@BAYER@", bayer_glsl())
