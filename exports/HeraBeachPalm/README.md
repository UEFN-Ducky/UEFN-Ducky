# Hera Beach Palm — UEFN export

Exported directly from the running UEFN editor through UEFN Ducky MCP on September 24, 2026.

- Visible mesh: `/Hera/Foliage/Trees/Beach_Palm/BakeData/SM_Hera_BeachPalm_A_Bake`
- The screenshot's `SM_Hera_BeachPalm_A_Fallback_ShadowProxy` is the selected actor's hidden shadow component, not its visible mesh.
- The FBX includes LOD0–LOD3 and collision. LOD0 contains 2,165 vertices and 3,773 triangles. Lower LODs and collision are hidden for viewing.
- Six 2048×2048 maps were exported through UEFN GPU render targets because this build's PNG Texture2D exporter was unavailable: bark base color, normal, SMR; leaves base color, normal, OSSR.
- The Blender bark and leaf materials use these maps, with leaf opacity from the mask red channel. Compressed two-channel normals were reconstructed and converted from DirectX to OpenGL for Blender. Textures are packed into the blend file.

The Blender materials are a reconstruction, not a portable Unreal shader graph. Leaf roughness uses the source material's 0.5 value. The optional distant impostor's separate shader is not reconstructed; use LOD0 for the complete visible tree.

## Animated version

Open `Hera_BeachPalm_UEFN_Animated.blend` for trunk and frond wind animation. The timeline covers 20 seconds (frames 1–601 at 30 fps). It contains 301 baked poses as native absolute shape keys on LOD0, interpolated linearly. All textures are packed; playback does not need Python or UEFN. The earlier `Hera_BeachPalm_UEFN.blend` is the static version.

The animation was translated from the actual exported `MF_TreeAnim_Apollo_Optimized` graph using the original pivot and X-vector textures, wind waveform, and material settings. It preserves separate pivots for 23 fronds. The source files and reusable evaluator are in `Animation/`.

This is a reconstruction of ordinary shader wind at the asset origin, using the captured wind direction/strength. Live storm, gust events, physics interactions, destruction, and landscape effects are not carried over. World-position-dependent phase differs from the tree's original island placement. The 20-second clip is not guaranteed seamless when the timeline restarts. Lower LODs and collision remain static and hidden.

Verification: evaluated Blender vertices match the translated graph at sampled baked frames within 0.002 cm; frame 2's interpolation error is 0.137 cm or less. Root movement throughout the bake stays below 0.001 cm. Vertex movement between frames 1 and 301 reaches 68.8 cm.

The original Blender startup objects remain in the file. The viewport isolates and frames LOD0 in Material Preview.
