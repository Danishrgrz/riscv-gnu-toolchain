import argparse
import os
from zipfile import ZipFile
import requests
from github import Auth, Github, Repository
from get_most_recent_ci_hash import gcc_hashes, get_valid_artifact_hash
from compare_testsuite_log import compare_logs


def parse_arguments():
    parser = argparse.ArgumentParser(description="Download valid log artifacts")
    parser.add_argument(
        "-hash",
        required=True,
        type=str,
        help="Commit hash of GCC to get artifacts for",
    )
    parser.add_argument(
        "-token",
        required=True,
        type=str,
        help="Github access token",
    )
    return parser.parse_args()


def get_possible_artifact_names():
    """
    Generates all possible permutations of target artifact logs and
    removes unsupported targets

    Current Support:
      Linux: rv32/64 multilib non-multilib
      Newlib: rv32/64 non-multilib
      Arch extensions: gc
    """
    libc = ["gcc-linux", "gcc-newlib"]
    arch = ["rv32{}-ilp32d-{}", "rv64{}-lp64d-{}"]
    multilib = ["multilib", "non-multilib"]

    arch_extensions = ["gc"]

    all_lists = [
        "-".join([i, j, k])
        for i in libc
        for j in arch
        for k in multilib
        if not ("rv32" in j and k == "multilib")
    ]

    all_names = [
        name.format(ext, "{}") for name in all_lists for ext in arch_extensions
    ]
    return all_names


def check_artifact_exists(artifact_name: str, repo: Repository):
    """
    @param artifact_name is the artifact associated with build success
    If the artifact does not exist, something failed and logs error into
    appropriate file; else returns artifact id for downloading
    """
    build_name = artifact_name
    build_failed = False
    artifact = repo.get_artifacts(artifact_name).get_page(0)
    # check if the build failed
    if len(artifact) == 0:
        print(f"build failed for {build_name}")
        build_failed = True
        with open("./logs/failed_build.txt", "a+") as f:
            f.write(f"{build_name}|Check logs\n")
    # check if the testsuite failed
    artifact_name += "-report.log"
    artifact = repo.get_artifacts(artifact_name).get_page(0)
    if len(artifact) == 0:
        print(f"testsuite failed for {build_name}")
        if not build_failed:
            with open("./logs/failed_testsuite.txt", "a+") as f:
                f.write(f"{build_name}|Cannot find testsuite artifact\n")
        return -1
    return artifact[0].id


def download_artifact(artifact_name: str, artifact_id: str, token: str):
    """
    Uses GitHub api endpoint to download and extract the log artifact into
    directory called ./logs
    """
    params = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "X-Github-Api-Version": "2022-11-28",
    }
    artifact_zip_name = artifact_name.replace(".log", ".zip")
    print(artifact_zip_name)
    print(f"downloading: {artifact_id}")
    r = requests.get(
        f"https://api.github.com/repos/patrick-rivos/riscv-gnu-toolchain/actions/artifacts/{artifact_id}/zip",
        headers=params,
    )
    print(r.status_code)
    with open(f"./{artifact_zip_name}", "wb") as f:
        f.write(r.content)
    with ZipFile(f"./{artifact_zip_name}", "r") as zf:
        zf.extractall(path=f"./{artifact_name.split('.log')[0]}")
    os.rename(
        f"./{artifact_name.split('.log')[0]}/{artifact_name}", f"./logs/{artifact_name}"
    )


def download_and_compare_all_artifacts(current_hash: str, token: str):
    """
    Goes through all possible artifact targets and downloads it
    if it exists. Downloads previous successful hash's artifact
    as well. Runs comparison on the downloaded artifacts
    """

    auth = Auth.Token(token)
    g = Github(auth=auth)

    repo = g.get_repo("patrick-rivos/riscv-gnu-toolchain")

    prev_commits = gcc_hashes(current_hash, False)
    artifact_names = get_possible_artifact_names()
    for artifact_name in artifact_names:
        artifact = artifact_name.format(current_hash)
        artifact_id = check_artifact_exists(artifact, repo)
        if artifact_id == -1:
            continue
        # comparison output path
        compare_path = f"./summaries/{artifact + '-report-summary.md'}"
        artifact += "-report.log"
        artifact_name += "-report.log"
        # download current artifact
        download_artifact(artifact, str(artifact_id), token)

        # download previous artifact
        base_hash, base_id = get_valid_artifact_hash(prev_commits, token, artifact_name)
        if base_hash == "No valid hash":
            print(f"no baseline for {artifact}")
            with open("./logs/no_baseline.txt", "a+") as f:
                f.write(f"{artifact}\n")
            base_hash = current_hash + "-no-baseline"
            try:
                compare_logs(
                    base_hash,
                    f"./logs/{artifact}",
                    base_hash,
                    f"./logs/{artifact}",
                    compare_path,
                )
            except (RuntimeError, ValueError) as err:
                with open("./logs/failed_testsuite.txt", "a+") as f:
                    f.write(f"{artifact}|{err}\n")
            continue

        download_artifact(artifact_name.format(base_hash), str(base_id), token)
        try:
            compare_logs(
                base_hash,
                f"./logs/{artifact_name.format(base_hash)}",
                current_hash,
                f"./logs/{artifact}",
                compare_path,
            )
        except (RuntimeError, ValueError) as err:
            with open("./logs/failed_testsuite.txt", "a+") as f:
                f.write(f"{artifact}|{err}\n")

    return


def main():
    args = parse_arguments()
    download_and_compare_all_artifacts(args.hash, args.token)


if __name__ == "__main__":
    main()