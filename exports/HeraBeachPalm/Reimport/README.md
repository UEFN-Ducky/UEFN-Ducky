# Custom UEFN foliage palm

Project folder: `/ExampleProject1/CustomFoliage/HeraPalm/`

- `SM_Custom_HeraPalm`: Blender mesh imported at the original centimeter scale, with three UV channels, full precision UVs and four simple trunk collision capsules.
- `FT_Custom_HeraPalm`: reusable paint preset. Density 1 per 100 square meters, minimum spacing 6 meters, uniform scale 0.85–1.15, slope up to 35 degrees, upright placement, blocking trunk collision, wind enabled.
- `M_Custom_HeraPalm_Bark` and `M_Custom_HeraPalm_Leaves`: project-owned materials using the six exported textures. Leaves use masked, two-sided foliage shading.
- `T_Custom_HeraPalm_WindVAT`: 4096×512 half-float vertex-displacement texture. UV channel 2 indexes the original Blender vertices. It contains the 20-second Blender wind bake plus a 2-second smooth transition back to the beginning. Both materials expose WindStrength, default 1.

Original Fortnite material references failed UEFN's asset reference validator. They were replaced with these independent project materials. Mesh, foliage type, both materials and the wind texture passed UEFN validation after replacement. The mesh has one advisory warning recommending additional LODs. Quality-level minimum LOD overrides are reset to zero. This does not represent a full island publish test.

The wind texture supports streaming and has generated mipmaps to meet UEFN validation requirements. Global full-mip residency is enabled, and the vertex shader samples mip zero explicitly with bilinear filtering so vertex IDs retain their exact animation samples.

The custom wind is a looping GPU vertex animation, not a skeletal animation and not a live Fortnite wind/storm simulation. Geometry simplification that interpolates vertex-ID UVs is incompatible with this bake; Nanite is disabled and the imported mesh retains its 3,773 triangles. Regenerate the VAT if topology changes.

`SM_Custom_HeraPalm.fbx` and `T_Custom_HeraPalm_WindVAT.exr` are the local reimport sources. The saved Blender animation remains in the parent folder.
