# Optional model assets

CCRaw supports photography upscaling, denoising/restoration, sky/person/foreground/depth selection and local SenseVoice speech recognition. Model weights are supplied in the separate verified runtime archive; inference remains optional and CPU fallback is available.

The metadata JSON files in assets/models describe the concrete model architecture, source and native scale. Third-party model names, sources and license texts are retained. The runtime manifest verifies each file, including speech model and tokens. See THIRD_PARTY.md and assets/*LICENSE*.txt for terms; the CCRaw MIT license applies to project code and does not replace model licenses.

Large models are not included in the lightweight Python wheel or source archive. The base package includes its Chinese font and OFL license so watermarking and the interface work without optional models. Maintainers may rebuild equivalent ONNX models with tools/convert_*.py; conversions must retain model provenance, precision tests and updated checksums.
