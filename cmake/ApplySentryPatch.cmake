if (NOT DEFINED SOURCE_DIR)
    message(FATAL_ERROR "SOURCE_DIR is required to patch sentry-native")
endif ()
find_package(Git REQUIRED)
set(patch_file "${CMAKE_CURRENT_LIST_DIR}/patches/libunwind-section-bounds.patch")
execute_process(COMMAND "${GIT_EXECUTABLE}" apply --check "${patch_file}"
    WORKING_DIRECTORY "${SOURCE_DIR}" RESULT_VARIABLE check_result
    OUTPUT_QUIET ERROR_QUIET)
if (check_result EQUAL 0)
    execute_process(COMMAND "${GIT_EXECUTABLE}" apply "${patch_file}"
        WORKING_DIRECTORY "${SOURCE_DIR}" COMMAND_ERROR_IS_FATAL ANY)
else ()
    # FetchContent can revisit an already patched checkout after reconfiguration.
    execute_process(COMMAND "${GIT_EXECUTABLE}" apply --reverse --check "${patch_file}"
        WORKING_DIRECTORY "${SOURCE_DIR}" RESULT_VARIABLE reverse_result
        OUTPUT_QUIET ERROR_QUIET)
    if (NOT reverse_result EQUAL 0)
        message(FATAL_ERROR "The libunwind bounds patch needs review for this sentry-native source")
    endif ()
endif ()
