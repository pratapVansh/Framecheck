# Optional clothing checkpoint

The default inspector can examine suitable isolated products without a neural model. Worn-garment localization additionally uses the existing pretrained `mattmdjaga/segformer_b2_clothes` checkpoint.

The source includes only the small checkpoint metadata files:

```text
models/segformer_b2_clothes/
  config.json
  preprocessor_config.json
```

To enable the local parser:

1. Install the optional dependencies from the project root: `python -m pip install ".[garment]"`.
2. Obtain the matching `model.safetensors` from [the upstream checkpoint](https://huggingface.co/mattmdjaga/segformer_b2_clothes/tree/main). Review its upstream terms before use.
3. Place it at `models/segformer_b2_clothes/model.safetensors` and restart the app.

Weights are deliberately ignored by Git. The loader runs locally on CPU, requires safe weights, checks for loading errors, and does not automatically download or train a model. Missing weights leave manual region marking available; they must not produce fake predictions.

SegFormer supplies candidate garment masks. DIP determines quality findings and the verdict. These masks are not human-verified references or validated product instances.
