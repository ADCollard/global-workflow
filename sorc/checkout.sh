#!/bin/sh
set -xue

while getopts "o" option;
do
 case $option in
  o)
   echo "Received -o flag for operations"
   ;;
  :)
   echo "option -$OPTARG needs an argument"
   ;;
  *)
   echo "invalid option -$OPTARG, exiting..."
   exit
   ;;
 esac
done

topdir=$(pwd)
echo $topdir

echo fv3gfs checkout ...
if [[ ! -d fv3gfs.fd ]] ; then
    rm -f ${topdir}/checkout-fv3gfs.log
    git clone --recursive --branch GFS.v16.3.1 https://github.com/ufs-community/ufs-weather-model.git fv3gfs.fd >> ${topdir}/checkout-fv3gfs.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory fv3gfs.fd already exists.'
fi

echo gsi checkout ...
if [[ ! -d gsi.fd ]] ; then
    rm -f ${topdir}/checkout-gsi.log
#    git clone --recursive --branch gfsda.v16.3.12 https://github.com/NOAA-EMC/GSI.git gsi.fd >> ${topdir}/checkout-gsi.log 2>&1
# Check out develop for now
    git clone --recursive https://github.com/NOAA-EMC/GSI.git gsi.fd >> ${topdir}/checkout-gsi.log 2>&1
    cd gsi.fd
    git submodule update --init
    cd ${topdir}
else
    echo 'Skip.  Directory gsi.fd already exists.'
fi

echo gsi_utils checkout ...
if [[ ! -d gsi_utils.fd ]] ; then
    rm -f ${topdir}/checkout-gsi_utils.log
# Check out a version before the changes for Thompson microphysics were introduced.
    git clone https://github.com/NOAA-EMC/GSI-Utils.git gsi_utils.fd >> ${topdir}/checkout-gsi_utils.log 2>&1
    cd gsi_utils.fd
    git checkout 2a15d3b514cb05a9c1343e437f134375ad260369
    cd ${topdir}
else
    echo 'Skip.  Directory gsi_utils.fd already exists.'
fi

echo gsi_monitor checkout ...
if [[ ! -d gsi_monitor.fd ]] ; then
    rm -f ${topdir}/checkout-gsi_monitor.log
# Check out a version before changes for the new directory structure were introduced.
    git clone https://github.com/NOAA-EMC/GSI-Monitor.git gsi_monitor.fd >> ${topdir}/checkout-gsi_monitor.log 2>&1
    cd gsi_monitor.fd
    git checkout e1f9f21af16ce912fdc2cd75c5b27094a550a0c5
    cd ${topdir}
else
    echo 'Skip.  Directory gsi_monitor.fd already exists.'
fi


echo gldas checkout ...
if [[ ! -d gldas.fd ]] ; then
    rm -f ${topdir}/checkout-gldas.log
    git clone --branch gldas_gfsv16_release.v.2.1.0 https://github.com/NOAA-EMC/GLDAS  gldas.fd >> ${topdir}/checkout-gldas.fd.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory gldas.fd already exists.'
fi

echo ufs_utils checkout ...
if [[ ! -d ufs_utils.fd ]] ; then
    rm -f ${topdir}/checkout-ufs_utils.log
    git clone --branch ops-gfsv16.3.20 https://github.com/ufs-community/UFS_UTILS ufs_utils.fd >> ${topdir}/checkout-ufs_utils.fd.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory ufs_utils.fd already exists.'
fi

echo EMC_post checkout ...
if [[ ! -d gfs_post.fd ]] ; then
    rm -f ${topdir}/checkout-gfs_post.log
    git clone --branch upp_v8.3.0 https://github.com/NOAA-EMC/UPP.git gfs_post.fd >> ${topdir}/checkout-gfs_post.log 2>&1
    cd ${topdir}
else
    echo 'Skip.  Directory gfs_post.fd already exists.'
fi

echo EMC_verif-global checkout ...
if [[ ! -d verif-global.fd ]] ; then
    rm -f ${topdir}/checkout-verif-global.log
    git clone --recursive --branch verif_global_v2.10.0 https://github.com/NOAA-EMC/EMC_verif-global.git verif-global.fd >> ${topdir}/checkout-verif-global.log 2>&1
    cd ${topdir}
else
    echo 'Skip. Directory verif-global.fd already exist.'
fi

exit 0
