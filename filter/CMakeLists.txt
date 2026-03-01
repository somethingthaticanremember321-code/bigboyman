cmake_minimum_required(VERSION 3.14)
project(marketplace_filter CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)
find_package(OpenSSL REQUIRED)

add_executable(filter main.cpp)

target_include_directories(filter PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(filter Threads::Threads OpenSSL::SSL OpenSSL::Crypto)

# Link standard libraries statically to avoid GLIBCXX version mismatches in Docker
target_link_options(filter PRIVATE -static-libstdc++ -static-libgcc)
target_compile_options(filter PRIVATE -O2 -Wall -Wextra)
