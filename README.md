# FLUX.1 Kontext

This repository wraps the **FLUX.1 Kontext** image‑to‑image model in a simple
[Cog](https://github.com/replicate/cog) predictor.  It loads the transformer, auto
encoder and text encoders and exposes a single `predict` function for applying
edits or style transfer to an input image.

FLUX.1 Kontext is an experimental model from Black Forest Labs.  The code in this
repository is licensed under Apache‑2.0.  See the model card for FLUX.1’s own
licence.

## Quick start

The easiest way to run the model is with Cog.  First install Cog and then run:

```bash
cog predict -i prompt="make the hair blue" -i input_image=@lady.png
```

All required model weights are downloaded automatically the first time you run
it.  The predictor uses `torch.compile` and caches the compiled model for faster
subsequent runs.

## Running the demo UI

A small [Gradio](https://gradio.app) demo is provided in `app.py`.  Launch it with:

```bash
python app.py
```

This starts a local web interface where you can experiment with different
prompts, aspect ratios and other options without using Cog directly.

## Predictor options

When calling `predict.py` (either via Cog or from another Python script) you can
control several parameters:

- `prompt` – text instruction describing how to modify the input image
- `input_image` – path to the source image (jpg, png, gif or webp)
- `aspect_ratio` – aspect ratio of the output. `match_input_image` keeps the
  original ratio
- `num_inference_steps` – number of denoising steps (4–50)
- `guidance` – guidance scale controlling prompt strength
- `seed` – optional random seed for repeatable results
- `output_format` – one of `webp`, `jpg` or `png`
- `output_quality` – quality value for jpg/webp outputs
- `disable_safety_checker` – skip NSFW filtering
- `go_fast` – enable the Taylor‐seer style cache for faster but potentially
  lower quality output

Calling the predictor returns the path to the generated image.

## Precompiling Torch code

The script `generate_torch_compile_cache.py` can be used to pre‑generate the
`torch.compile` cache so that the first inference call is faster.  This is
optional – if the cache file is absent the predictor will build it on the first
run.

## License

The wrapper code in this repository is released under the Apache‑2.0 license.
Model weights are provided by Black Forest Labs and are subject to their own
terms.
