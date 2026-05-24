from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_workflow(name: str) -> str:
    return (REPO_ROOT / ".github" / "workflows" / name).read_text()


def test_ci_runs_repository_and_dependency_hardening_checks() -> None:
    ci = read_workflow("ci.yml")

    required_snippets = [
        "name: security hardening",
        "make security-sdk",
        "make -C studio/backend security",
        "make security-frontend",
        "aquasecurity/trivy-action@v0.36.0",
        "gitleaks/gitleaks-action@v2",
        "semgrep/semgrep-action@v1",
        "config: .semgrep.yml",
    ]

    for snippet in required_snippets:
        assert snippet in ci


def test_ci_scans_and_hardens_studio_images_before_and_after_publish() -> None:
    ci = read_workflow("ci.yml")

    image_refs = [
        "relayna-studio-backend:latest",
        "relayna-studio-frontend:latest",
        "relayna-studio-backend@${{ steps.studio-backend-build.outputs.digest }}",
        "relayna-studio-frontend@${{ steps.studio-frontend-build.outputs.digest }}",
    ]
    hardening_steps = [
        "anchore/sbom-action/download-syft@v0",
        "sigstore/cosign-installer@v3",
        "cosign sign",
        "actions/attest-build-provenance@v2",
        "provenance: true",
    ]

    for snippet in image_refs + hardening_steps:
        assert snippet in ci


def test_release_hardens_sdk_artifacts_and_studio_images() -> None:
    release = read_workflow("release.yml")

    sdk_snippets = [
        "relayna-sdk-${{ github.ref_name }}.spdx.json",
        "subject-path: dist/*",
        "dist/*.tar.gz",
        "dist/*.whl",
    ]
    image_snippets = [
        "relayna-studio-backend-${{ github.ref_name }}.spdx.json",
        "relayna-studio-frontend-${{ github.ref_name }}.spdx.json",
        "Scan Studio backend image with Trivy",
        "Scan Studio frontend image with Trivy",
        "Sign Studio backend image",
        "Sign Studio frontend image",
        "Attest Studio backend image provenance",
        "Attest Studio frontend image provenance",
        "id-token: write",
        "attestations: write",
    ]

    for snippet in sdk_snippets + image_snippets:
        assert snippet in release
