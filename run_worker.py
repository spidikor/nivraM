"""Bounded local scientific worker; frozen as a console binary, launched hidden."""
import multiprocessing
import sys

if __name__ == '__main__':
    multiprocessing.freeze_support()
    if sys.argv[1:] == ['--verify-optical-model']:
        from nivram.optical_setup_worker import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--validate-optical']:
        from nivram.optical_validation import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--prepare-import']:
        from nivram.import_preparation import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--supplied-validation']:
        from nivram.supplied_validation import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--coordinate-recovery']:
        from nivram.coordinate_recovery import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--conformer']:
        from nivram.conformer import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--name']:
        from nivram.naming import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--pubchem']:
        from nivram.pubchem import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--png-metadata']:
        from nivram.image_jobs import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--png-preview']:
        from nivram.png_preview_jobs import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--populations']:
        from nivram.population_jobs import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--native-dialog-fixture']:
        from nivram.native_dialog_worker import worker_main
        raise SystemExit(worker_main())
    if sys.argv[1:] == ['--native-dialog-fixture-progress']:
        from nivram.native_dialog_worker import worker_main
        raise SystemExit(worker_main(progress=True))
    raise SystemExit('Unsupported local worker operation.')
